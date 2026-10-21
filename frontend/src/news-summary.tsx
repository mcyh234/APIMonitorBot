import React, { useEffect, useRef, useState } from "react";
import { BookOpen, Download, FileText, RefreshCw, Save, Search, Send, Trash2, X } from "lucide-react";

type RequestJSON = <T>(url: string, init?: RequestInit) => Promise<T>;
type Settings = { enabled: boolean; every_posts: number; max_wait_minutes: number; detail: string; timeline_enabled: boolean; connections_enabled: boolean; memory_enabled: boolean; memory_days: number; memory_items: number; context_items: number; focus: string };
type Model = { available: boolean; config_name: string; model: string | null };
type Batch = { id: number; target: string; status: string; created_at: string; sent_at: string | null; pages_sent: number; page_count: number; post_count: number; memory_count: number; attempts: number; retry_at: string | null; last_error: string | null; blocked_reason: string | null };
type Target = { target: string; waiting_count: number; memory_count: number; last_sent_at: string | null; batch: Batch | null };
type View = { settings: Settings; model: Model; review_model: Model; watch_enabled: boolean; selected_model_id: number | null; models: { id: number; name: string; model: string; enabled: boolean }[]; targets: Target[]; history: Batch[] };
type Source = { account: string; url: string; published_at: string | null; delivered_at: string; original: string; translated_zh: string; historical?: boolean };
type Document = { title: string; markdown: string; timelines: { title: string; nodes: { text: string; source_ids: string[] }[] }[]; connections: { relation: string; explanation: string; current_ids: string[]; history_ids: string[] }[] };
type Detail = { batch: Batch; document: Document; sources: Record<string, Source>; images: string[] };
const time = (value: string | null) => value ? new Date(value).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai" }) : "暂无";
const names: Record<string, string> = { pending: "等待生成或投递", sent: "已送达", blocked: "审核拦截", cancelled: "已取消" };

function exportMarkdown(detail: Detail) {
  const { document, sources } = detail;
  const parts = [`# ${document.title}`, document.markdown];
  for (const event of document.timelines) {
    parts.push(`## 事件时间线：${event.title}`);
    const stamp = (key: string) => Date.parse(sources[key].published_at || sources[key].delivered_at);
    for (const node of [...event.nodes].sort((a, b) => Math.min(...a.source_ids.map(stamp)) - Math.min(...b.source_ids.map(stamp)))) {
      const first = [...node.source_ids].sort((a, b) => stamp(a) - stamp(b))[0];
      parts.push(`- ${time(sources[first].published_at || sources[first].delivered_at)}：${node.text} ${node.source_ids.map(key => `[${key}]`).join(" ")}`);
    }
  }
  if (document.connections.length) parts.push("## 历史关联", ...document.connections.map(item => `- **${item.relation}**：${item.explanation} ${[...item.current_ids, ...item.history_ids].map(key => `[${key}]`).join(" ")}`));
  parts.push("## 来源", ...Object.entries(sources).map(([key, source]) => `[${key}]: ${source.url} "@${source.account}${source.historical ? " · 历史" : ""}"`));
  const url = URL.createObjectURL(new Blob([parts.join("\n\n")], { type: "text/markdown;charset=utf-8" }));
  const anchor = window.document.createElement("a"); anchor.href = url; anchor.download = `news-summary-${detail.batch.id}.md`; anchor.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function NewsSummaryPanel({ request }: { request: RequestJSON }) {
  const [view, setView] = useState<View | null>(null);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [modelId, setModelId] = useState("default");
  const [target, setTarget] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [detail, setDetail] = useState<Detail | null>(null);
  const [memory, setMemory] = useState<Source[] | null>(null);
  const [search, setSearch] = useState("");
  const dialog = useRef<HTMLDialogElement>(null);
  const previewOpener = useRef<HTMLButtonElement | null>(null);
  function apply(value: View, edit = false) {
    setView(value);
    if (edit) { setSettings(value.settings); setModelId(value.selected_model_id ? String(value.selected_model_id) : "default"); }
    setTarget(previous => value.targets.some(item => item.target === previous) ? previous : value.targets[0]?.target || "");
  }
  useEffect(() => {
    let active = true;
    void request<View>("/api/news-summary").then(value => { if (active) apply(value, true); }).catch(() => { if (active) setMessage("新闻总结加载失败，请刷新重试。"); });
    const timer = window.setInterval(() => { void request<View>("/api/news-summary").then(value => { if (active) apply(value); }).catch(() => {}); }, 30000);
    return () => { active = false; window.clearInterval(timer); };
  }, [request]);
  useEffect(() => {
    if (detail && !dialog.current?.open) dialog.current?.showModal();
  }, [detail]);
  async function action(work: () => Promise<void>) {
    setBusy(true); setMessage("");
    try { await work(); } catch (error) { setMessage(error instanceof Error ? error.message : "操作失败，请稍后重试。"); }
    finally { setBusy(false); }
  }
  return <details className="news-summary-panel">
    <summary>新闻总结与事件记忆</summary>
    <p>每个通知对象独立累计成功送达的新闻，达到条数后追加大模型总结图片。支持 Markdown、事件时间线、历史关联和失败续传；总结图片不计入下一轮。</p>
    {view && settings && <>
      <p role="status">总结模型：{view.model.available ? `${view.model.config_name} · ${view.model.model}` : "未配置或不可用，暂停总结"}。审核模型：{view.review_model.available ? view.review_model.config_name : "未配置或不可用，暂停总结"}。管理员也可用 /summarymodel list、/summarymodel 配置ID 选择。</p>
      {!view.watch_enabled && <p className="extension-message">AI 消息监听当前关闭；总结任务会等待监听恢复。</p>}
      <form onSubmit={event => { event.preventDefault(); void action(async () => {
        let updated = await request<View>("/api/news-summary", { method: "PUT", body: JSON.stringify(settings) });
        if (modelId !== (updated.selected_model_id ? String(updated.selected_model_id) : "default")) updated = await request<View>("/api/news-summary/model", { method: "PUT", body: JSON.stringify({ selector: modelId }) });
        apply(updated, true); setMessage("新闻总结设置已保存");
      }); }}>
        <div className="extension-grid">
          <label className="extension-toggle"><input type="checkbox" checked={settings.enabled} onChange={event => setSettings({ ...settings, enabled: event.target.checked })} />启用新闻总结</label>
          <label>每累计几条新闻总结<input type="number" min={1} max={100} required value={settings.every_posts} onChange={event => setSettings({ ...settings, every_posts: Number(event.target.value) })} /></label>
          <label>未满条数最长等待（分钟，0 关闭）<input type="number" min={0} max={1440} required value={settings.max_wait_minutes} onChange={event => setSettings({ ...settings, max_wait_minutes: Number(event.target.value) })} /></label>
          <label>总结使用的监测配置<select aria-label="总结使用的监测配置" value={modelId} onChange={event => setModelId(event.target.value)}><option value="default">跟随审核模型</option>{view.selected_model_id && !view.models.some(model => model.id === view.selected_model_id) && <option value={String(view.selected_model_id)}>所选配置已删除</option>}{view.models.map(model => <option key={model.id} value={String(model.id)} disabled={!model.enabled}>{model.name} · {model.model}{model.enabled ? "" : "（停用）"}</option>)}</select></label>
          <label>总结详细程度<select aria-label="总结详细程度" value={settings.detail} onChange={event => setSettings({ ...settings, detail: event.target.value })}><option value="brief">简洁快览</option><option value="standard">标准总结</option><option value="detailed">详细分析</option></select></label>
          <label className="extension-toggle"><input type="checkbox" checked={settings.timeline_enabled} onChange={event => setSettings({ ...settings, timeline_enabled: event.target.checked })} />展示事件时间线</label>
          <label className="extension-toggle"><input type="checkbox" checked={settings.connections_enabled} onChange={event => setSettings({ ...settings, connections_enabled: event.target.checked })} />展示历史新闻关联</label>
        </div>
        <label className="news-summary-focus">关注主题（可选）<textarea maxLength={500} value={settings.focus} placeholder="例如：编程代理、API 更新、模型能力变化" onChange={event => setSettings({ ...settings, focus: event.target.value })} /></label>
        <details><summary>历史记忆与保留策略</summary>
          <p>只使用同一通知对象已总结新闻的加密片段，按关键词和产品名检索。关闭记忆会删除已保留的新闻记忆与总结正文；未总结的新帖保留。单个对象最多积压 2000 条，过期内容按期限清理。</p>
          <div className="extension-grid">
            <label className="extension-toggle"><input type="checkbox" checked={settings.memory_enabled} onChange={event => setSettings({ ...settings, memory_enabled: event.target.checked })} />启用历史记忆</label>
            {([["memory_days", "记忆与历史保留天数", 1, 365], ["memory_items", "每个对象最多保留的记忆条数", 10, 1000], ["context_items", "每次最多关联的历史新闻", 1, 30]] as const).map(([key, label, min, max]) => <label key={key}>{label}<input type="number" min={min} max={max} required value={settings[key]} onChange={event => setSettings({ ...settings, [key]: Number(event.target.value) })} /></label>)}
          </div>
        </details>
        <div className="extension-actions"><button disabled={busy}><Save size={16} aria-hidden="true" />保存新闻总结</button><button type="button" disabled={busy} onClick={() => void action(async () => { apply(await request<View>("/api/news-summary"), true); setMessage("总结状态已刷新"); })}><RefreshCw size={16} aria-hidden="true" />刷新总结状态</button></div>
      </form>
      <div className="summary-targets">{view.targets.map(item => <article key={item.target}>
        <strong>{item.target}</strong><span>待总结 {item.waiting_count} / {view.settings.every_posts} 条 · 历史记忆 {item.memory_count} 条</span>
        <progress aria-label={`${item.target} 总结进度`} max={view.settings.every_posts} value={Math.min(item.waiting_count, view.settings.every_posts)} />
        <small>最近总结：{time(item.last_sent_at)}</small>
        {item.batch && <p>任务 #{item.batch.id} · 本批 {item.batch.post_count} 条 · 已投递 {item.batch.pages_sent}/{item.batch.page_count || "待生成"} 页{item.batch.last_error ? ` · ${item.batch.last_error}` : ""}{item.batch.retry_at ? ` · 下次重试 ${time(item.batch.retry_at)}` : ""}</p>}
        <button type="button" disabled={busy || !view.settings.enabled || (!item.waiting_count && !item.batch)} onClick={() => void action(async () => { const result = await request<{ view: View }>("/api/news-summary/run", { method: "POST", body: JSON.stringify({ target: item.target }) }); apply(result.view); setMessage(`${item.target} 总结任务已排队，生成并审核后发送`); })}><Send size={16} aria-hidden="true" />{item.batch ? "立即重试任务" : "提前总结本对象"}</button>
      </article>)}</div>
      {!view.targets.length && <p>请先在「AI 消息监听」配置群聊或私聊通知对象。</p>}
      <details><summary>记忆检索与清理</summary>
        <div className="extension-grid"><label>查看哪个通知对象<select aria-label="查看哪个通知对象" value={target} onChange={event => { setTarget(event.target.value); setMemory(null); }}>{view.targets.map(item => <option key={item.target}>{item.target}</option>)}</select></label><label>检索记忆中的关键词<input maxLength={100} value={search} onChange={event => setSearch(event.target.value)} placeholder="产品名、模型、事件关键词" /></label></div>
        <div className="extension-actions"><button disabled={busy || !target} onClick={() => void action(async () => { const result = await request<{ items: Source[] }>(`/api/news-summary/memory?target=${encodeURIComponent(target)}&q=${encodeURIComponent(search)}`); setMemory(result.items); setMessage(`找到 ${result.items.length} 条记忆（最多显示 40 条）`); })}><Search size={16} aria-hidden="true" />检索加密记忆</button>
          <button disabled={busy || !target} onClick={() => { if (window.confirm(`清空 ${target} 的历史新闻记忆和总结正文？待总结的新帖会保留。`)) void action(async () => { apply(await request<View>("/api/news-summary/memory/clear", { method: "POST", body: JSON.stringify({ target }) })); setMemory(null); setMessage(`${target} 的历史记忆与总结正文已清空`); }); }}><Trash2 size={16} aria-hidden="true" />清空本对象历史记忆</button></div>
        {memory && <div className="summary-memory-list">{memory.map(source => <article key={source.url}><a href={source.url} target="_blank" rel="noreferrer">@{source.account} · {time(source.published_at || source.delivered_at)}</a><p>{source.translated_zh}</p><details><summary>查看原文片段</summary><p>{source.original}</p></details></article>)}{!memory.length && <p>暂无符合条件的记忆。</p>}</div>}
      </details>
      <details><summary>总结历史 · 最近 {view.history.length} 批</summary>
        <div className="summary-history">{view.history.map(batch => <article key={batch.id}><div><strong>#{batch.id} · {batch.target}</strong><span>{names[batch.status] || batch.status} · {batch.post_count} 条新闻 · 关联 {batch.memory_count} 条历史</span><small>{time(batch.created_at)}{batch.blocked_reason ? ` · ${batch.blocked_reason}` : ""}</small></div><button disabled={busy || batch.status !== "sent"} onClick={event => { previewOpener.current = event.currentTarget; void action(async () => { setDetail(await request<Detail>(`/api/news-summary/history/${batch.id}`)); }); }}><BookOpen size={16} aria-hidden="true" />查看总结图片</button></article>)}{!view.history.length && <p>启用后将从此后成功送达的新闻开始统计。</p>}</div>
      </details>
    </>}
    {message && <p role="status" className="extension-message">{message}</p>}
    {detail && <dialog ref={dialog} className="news-summary-dialog" aria-labelledby="summary-dialog-title" onClose={() => { setDetail(null); window.setTimeout(() => previewOpener.current?.focus(), 0); }} onCancel={event => { event.preventDefault(); dialog.current?.close(); }}>
      <div className="summary-dialog-heading"><h3 id="summary-dialog-title">{detail.document.title}</h3><button autoFocus aria-label="关闭总结预览" onClick={() => dialog.current?.close()}><X size={20} aria-hidden="true" /></button></div>
      <p>{detail.batch.target} · {detail.batch.post_count} 条新闻 · {detail.images.length} 页</p>
      <div className="extension-actions"><button onClick={() => exportMarkdown(detail)}><Download size={16} aria-hidden="true" />导出 Markdown</button></div>
      {detail.images.map((image, index) => <img key={index} src={`data:image/png;base64,${image}`} alt={`新闻总结第 ${index + 1} 页`} />)}
      <details><summary><FileText size={16} aria-hidden="true" />查看 Markdown 原文</summary><pre>{detail.document.markdown}</pre></details>
    </dialog>}
  </details>;
}
