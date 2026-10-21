"""Bounded, content-free news watcher journal, persisted separately from delivery ACKs."""
from __future__ import annotations

import json
import re
from datetime import datetime

from backend.app.app_settings import get_app_setting, set_app_setting
from backend.app.time_utils import utc_now

KEY = "news.watch.diagnostics"
LIMIT = 300
SOURCE_REASONS = {"retry_after": "上游指定等待", "http_error": "HTTP 错误", "timeout": "请求或排队超时", "network_or_parse": "网络或订阅解析失败", "queue_full": "来源池繁忙"}
SOURCE_SCOPES = {"host": "整个来源域名", "account": "本账号的该来源"}
LABELS = {
    "started": "监听轮次开始", "finished": "监听轮次完成", "disabled": "监听关闭",
    "busy": "上一轮仍在处理", "crashed": "监听轮次异常，等待调度重试",
    "fetching": "抓取来源", "fetched": "来源抓取完成", "fetch_failed": "来源抓取失败或超时",
    "baseline": "首次建立基线，不补发历史", "discovered": "发现新帖并加入待发送队列",
    "preparing": "读取帖子详情与翻译", "reviewing": "审核原文、译文与上下文",
    "checking_model": "检查审核模型配置",
    "rendering": "生成新闻图片", "sending": "等待通知发送确认", "sent": "通知已确认送达",
    "blocked": "内容审核明确拦截", "model_unavailable": "审核或翻译不可用，保留加密任务重试",
    "failed": "当前阶段失败，保留加密任务重试", "timeout": "当前阶段超时，保留加密任务重试",
    "invalidated": "设置变化，本轮工作失效", "retry_requested": "管理员请求立即重试待发送项",
}


def _sanitize(stored):
    # Rebuild from a strict allowlist, including when reading data from older versions.
    events = []
    for item in stored.get("events", [])[-LIMIT:]:
        if not isinstance(item, dict) or item.get("code") not in LABELS:
            continue
        try:
            at = datetime.fromisoformat(item["at"])
            if at.tzinfo is None:
                continue
        except (ValueError, TypeError, KeyError):
            continue
        event = {"code": item["code"], "at": at.isoformat(), "message": LABELS[item["code"]]}
        for key, pattern in (("account", r"[A-Za-z0-9_]{1,15}"), ("post_id", r"[0-9]{1,30}"), ("target", r"[GP][0-9]{1,20}")):
            if re.fullmatch(pattern, str(item.get(key, ""))):
                event[key] = str(item[key])
        for key in ("count", "elapsed_ms"):
            if isinstance(item.get(key), int):
                event[key] = max(0, min(item[key], 10**9))
        if item.get("stage") in LABELS:
            event["stage"] = item["stage"]
            event["stage_label"] = LABELS[item["stage"]]
        from backend.app.news_filter import REASONS
        if item.get("reason") in REASONS:
            event["reason"] = item["reason"]
            event["reason_label"] = REASONS[item["reason"]]
        if item["code"] == "blocked":
            # Moderation audit retains only counts, timestamp and fixed category.
            event = {key: value for key, value in event.items() if key in {"code", "at", "message", "reason", "reason_label"}}
            event["count"] = 1
        events.append(event)
    return {"events": events}


def read_journal(session):
    return _sanitize(json.loads(get_app_setting(session, KEY) or "{}"))


def record(session, code, **metadata):
    if code not in LABELS:
        raise ValueError("Unknown news diagnostic code")
    journal = read_journal(session)
    journal["events"].append({"code": code, "at": utc_now().isoformat(), **metadata})
    if [item["code"] for item in journal["events"][-4:]] == ["started", "finished", "started", "finished"]:
        # Preserve a current heartbeat without evicting failures during idle polls.
        del journal["events"][-4:-2]
    # Sanitize writes through exactly the same schema as reads.
    set_app_setting(session, KEY, json.dumps(_sanitize(journal), ensure_ascii=False))


def diagnostics_view(session):
    from backend.app.tibo_watch import read_watch_settings, read_watch_state
    from backend.app.news_filter import moderation_view
    settings, state, now = read_watch_settings(session), read_watch_state(session), utc_now()
    events = read_journal(session)["events"]
    start = next((item for item in reversed(events) if item["code"] == "started"), None)
    terminal = next((item for item in reversed(events) if item["code"] in {"finished", "crashed", "disabled", "invalidated"}), None)
    running = bool(start and (terminal is None or start["at"] > terminal["at"]))
    stage = next((item for item in reversed(events) if item["code"] not in {"busy"}), None)
    age = max(0, int((now - datetime.fromisoformat(start["at"])).total_seconds())) if start else None
    pending = [item for item in state.pending if item.account in settings.accounts and item.target in settings.targets.split("&")]
    waiting = sum(bool(item.retry_at and item.retry_at > now) for item in pending)
    model = moderation_view(session)
    warnings = []
    if not settings.enabled:
        warnings.append("监听已关闭。")
    elif not model["model_configured"]:
        warnings.append("审核模型未配置或不可用，投递暂停；使用 /reviewmodel 检查。")
    if settings.enabled and (age is None or age > 900):
        warnings.append("近期无新轮次启动，请检查服务进程和调度器；重启前的阶段记录也可能保留。")
    if running and age is not None and age > 180:
        warnings.append("当前轮次耗时较长，请检查最近阶段和队列重试时间。")
    if waiting:
        warnings.append(f"{waiting} 个投递正在退避等待；同账号同对象按顺序发送，前帖失败会延后后帖。")
    if settings.enabled and not model["translation_model"]["available"]:
        warnings.append("所选翻译模型不可用，投递暂停；使用 /translatemodel 检查。")
    accounts = []
    for username in settings.accounts:
        item = state.accounts.get(username)
        sources = []
        for source in (item.sources if item else [])[:12]:
            if not re.fullmatch(r"[A-Za-z0-9.-]{1,253}", str(source.get("host", ""))):
                continue
            if source.get("state") not in {"ok", "cached", "error", "cooldown", "rate_limited"}:
                continue
            sources.append({"host": source["host"], "state": source["state"],
                            **{key: source[key] for key in ("post_count", "http_status", "retry_seconds") if isinstance(source.get(key), int)}})
            if source.get("reason") in SOURCE_REASONS:
                sources[-1]["reason_label"] = SOURCE_REASONS[source["reason"]]
            if source.get("scope") in SOURCE_SCOPES:
                sources[-1]["scope_label"] = SOURCE_SCOPES[source["scope"]]
        accounts.append({"account": username, "cursor": item.cursor if item else None,
                         "last_completed_at": item.last_completed_at if item else None,
                         "sources": sources,
                         "fetch_failed": bool(item and item.last_error),
                         "pending_count": sum(row.account == username for row in pending)})
    return {"events": list(reversed(events)), "running": running, "round_age_seconds": age,
            "accounts": accounts,
            "stage": stage, "warnings": warnings, "pending_count": len(pending), "retry_waiting": waiting,
            "pending": [{"account": item.account, "post_id": item.post_id, "target": item.target,
                         "stage_label": LABELS.get(item.last_stage), "failure_label": LABELS.get(item.failure_code),
                         "attempts": item.attempts, "retry_at": item.retry_at} for item in pending[:100]],
            "last_sent_at": state.last_sent_at, "blocked_count": state.blocked_count}


def format_diagnostics(session, account=None):
    view = diagnostics_view(session)
    lines = ["AI 新闻监听诊断", *view["warnings"],
             f"待发送 {view['pending_count']} 个投递 · 退避 {view['retry_waiting']} · 累计拦截 {view['blocked_count']}",
             f"最近送达：{view['last_sent_at'] or '暂无'}", "最近日志（时间为 UTC）："]
    for row in [row for row in view["accounts"] if row["account"] == account or (not account and row["fetch_failed"])][:5]:
        lines.append(f"@{row['account']} 最近抓取完成：{row['last_completed_at'] or '暂无'} · 最新ID：{row['cursor'] or '未建立基线'} · {'抓取失败' if row['fetch_failed'] else '抓取正常'} · 待发送 {row['pending_count']}")
        for source in row["sources"]:
            lines.append(f"  {source['host']} {source['state']} · {source.get('post_count', 0)} 条 · 退避 {source.get('retry_seconds', 0)} 秒"
                         f" · {source.get('scope_label', '旧记录，范围未知')} · {source.get('reason_label', '原因未记录')}"
                         + (f" · HTTP {source['http_status']}" if 'http_status' in source else ""))
    for row in [row for row in view["pending"] if not account or row["account"] == account][:5]:
        lines.append(f"待发送 @{row['account']} {row['post_id']} → {row['target']} · 失败 {row['attempts']} 次 · {row['stage_label'] or '尚无失败阶段'} · 重试时间 {row['retry_at'] or '下一轮'}")
    events = [item for item in view["events"] if not account or item.get("account") == account]
    for item in events[:15]:
        meta = " ".join(str(item[key]) for key in ("account", "post_id", "target") if key in item)
        detail = f" · {LABELS[item['stage']]}" if "stage" in item else ""
        from backend.app.news_filter import REASONS
        if "reason" in item:
            detail += f" · {REASONS[item['reason']]}"
        if "count" in item:
            detail += f" · {item['count']} 条"
        lines.append(f"{item['at']} {item['message']} {meta}{detail}")
    if not events:
        lines.append("暂无日志；升级并重启服务后开始记录。")
    lines.append("/newswatch logs [@账号] 查看；/newswatch retry 提前重试，不跳过审核或重发历史。")
    return "\n".join(lines)
