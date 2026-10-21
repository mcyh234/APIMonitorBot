import React, { useEffect, useState } from "react";
import { Download, RefreshCw } from "lucide-react";

type Event = { code: string; at: string; message: string; account?: string; post_id?: string; target?: string; count?: number; elapsed_ms?: number; stage_label?: string; reason_label?: string };
type Diagnostic = { events: Event[]; running: boolean; round_age_seconds: number | null; stage: Event | null; warnings: string[]; pending_count: number; retry_waiting: number; pending: { account: string; post_id: string; target: string; attempts: number; retry_at: string | null; stage_label: string | null; failure_label: string | null }[] };
type RequestJSON = <T>(url: string, init?: RequestInit) => Promise<T>;
const time = (value: string | null) => value ? new Date(value).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai" }) : "暂无";

export function NewsDiagnostics({ request }: { request: RequestJSON }) {
  const [open, setOpen] = useState(false);
  const [view, setView] = useState<Diagnostic | null>(null);
  const [account, setAccount] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const load = async (retry = false) => {
    setBusy(true);
    try {
      setView(await request<Diagnostic>(`/api/tibo-watch/${retry ? "retry" : "logs"}`, retry ? { method: "POST" } : undefined));
      setMessage(retry ? "已安排下一轮重试，仍需通过审核；不会重发历史。" : "监听日志已刷新。");
    } catch { setMessage("日志加载失败，请检查服务连接。"); }
    finally { setBusy(false); }
  };
  useEffect(() => {
    if (!open) return;
    let active = true;
    const refresh = () => { void request<Diagnostic>("/api/tibo-watch/logs").then(data => { if (active) setView(data); }).catch(() => { if (active) setMessage("日志加载失败，请检查服务连接。"); }); };
    refresh(); const timer = window.setInterval(refresh, 10000);
    return () => { active = false; window.clearInterval(timer); };
  }, [open, request]);
  const selected = account.trim().replace(/^@/, "").toLowerCase();
  const events = view?.events.filter(item => !selected || item.account?.toLowerCase().includes(selected)) || [];
  return <details className="news-diagnostics" onToggle={event => setOpen(event.currentTarget.open)}>
    <summary>新闻监听日志与排障</summary>
    <p>最近 300 条阶段记录，展开时每 10 秒刷新。只记录账号、帖子 ID、阶段、计数与固定原因，不保存正文或模型回复。聊天：/newswatch logs [@账号]。</p>
    <div className="extension-actions">
      <button type="button" disabled={busy} onClick={() => void load()}><RefreshCw size={16} />刷新监听日志</button>
      <button type="button" disabled={busy || !view?.pending_count} onClick={() => void load(true)}>立即重试待发送项</button>
      <button type="button" disabled={!view} onClick={() => {
        if (!view) return;
        const url = URL.createObjectURL(new Blob([JSON.stringify(view, null, 2)], { type: "application/json" }));
        const link = document.createElement("a"); link.href = url; link.download = "news-watch-diagnostics.json"; link.click();
        window.setTimeout(() => URL.revokeObjectURL(url), 1000);
      }}><Download size={16} />导出诊断日志</button>
    </div>
    {message && <p role="status">{message}</p>}
    {view && <>
      <p>轮次：{view.running ? `处理中 · 已耗时 ${view.round_age_seconds ?? 0} 秒` : "等待下一轮"} · 最近阶段：{view.stage?.message || "暂无"} · 待发送 {view.pending_count} · 退避等待 {view.retry_waiting}</p>
      {view.warnings.map(warning => <p key={warning} className="extension-message">{warning}</p>)}
      <label>按 X 账号筛选日志<input type="search" value={account} onChange={event => setAccount(event.target.value)} placeholder="@thsottiaux" maxLength={16} /></label>
      <div className="news-account-grid">{events.slice(0, 60).map((item, index) => <div className="news-account-state" key={`${item.at}-${index}`}>
        <strong>{item.message}</strong><span>{time(item.at)}{item.account ? ` · @${item.account}` : ""}</span>
        <span>{item.post_id ? `帖子 ${item.post_id} ` : ""}{item.target || ""}{item.count !== undefined ? ` · ${item.count} 条` : ""}{item.elapsed_ms !== undefined ? ` · ${(item.elapsed_ms / 1000).toFixed(1)} 秒` : ""}</span>
        {(item.stage_label || item.reason_label) && <span>阶段：{item.stage_label || "本地过滤"}{item.reason_label ? ` · 分类：${item.reason_label}` : ""}</span>}
      </div>)}</div>
      {!events.length && <p>暂无匹配日志；升级并重启服务后开始记录。</p>}
      <details><summary>待发送队列（最多展示 100 项）</summary><div className="news-account-grid">{view.pending.map(item => <div className="news-account-state" key={`${item.account}-${item.post_id}-${item.target}`}>
        <strong>@{item.account} · {item.target}</strong><span>帖子 {item.post_id} · 已失败 {item.attempts} 次</span><span>失败阶段：{item.stage_label || "尚无记录"}{item.failure_label ? ` · ${item.failure_label}` : ""}</span><span>下次重试：{item.retry_at ? time(item.retry_at) : "下一轮"}</span>
      </div>)}</div></details>
    </>}
  </details>;
}
