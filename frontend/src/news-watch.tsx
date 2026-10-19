import React, { useEffect, useState } from "react";
import { Save, RefreshCw, ExternalLink, Plus, X } from "lucide-react";

type RequestJSON = <T>(url: string, init?: RequestInit) => Promise<T>;
type Config = { enabled: boolean; targets: string; interval_seconds: number; accounts: string[] };
type Account = { username: string; name: string; company: string; category: string; reason: string; recommended: boolean; url: string };
type AccountStatus = { username: string; cursor: string | null; last_checked_at: string | null; last_error: string | null; pending_count: number };
type Status = { initialized: boolean; last_checked_at: string | null; last_sent_at: string | null; pending_count: number; last_error: string | null; accounts: AccountStatus[] };
type View = { settings: Config; status: Status; catalog: Account[] };
const categories = ["公司", "产品", "负责人", "创始人"];
const time = (value: string | null) => value ? new Date(value).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai" }) : "暂无";

export function NewsWatchPanel({ request }: { request: RequestJSON }) {
  const [config, setConfig] = useState<Config | null>(null);
  const [status, setStatus] = useState<Status | null>(null);
  const [catalog, setCatalog] = useState<Account[]>([]);
  const [search, setSearch] = useState("");
  const [custom, setCustom] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const apply = (view: View) => { setConfig(view.settings); setStatus(view.status); setCatalog(view.catalog); };
  useEffect(() => {
    let active = true;
    void request<View>("/api/tibo-watch").then(view => { if (active) apply(view); })
      .catch(() => { if (active) setMessage("AI 消息监听加载失败，请刷新页面重试。"); });
    const timer = window.setInterval(() => {
      void request<View>("/api/tibo-watch").then(view => { if (active) setStatus(view.status); }).catch(() => {});
    }, 30000);
    return () => { active = false; window.clearInterval(timer); };
  }, [request]);
  function select(accounts: string[]) {
    if (config) setConfig({ ...config, accounts: [...new Set(accounts.map(value => value.toLowerCase()))] });
  }
  function toggle(username: string, checked: boolean) {
    if (config) select(checked ? [...config.accounts, username] : config.accounts.filter(value => value !== username.toLowerCase()));
  }
  function addCustom() {
    const username = custom.trim().replace(/^@/, "");
    if (!/^[A-Za-z0-9_]{1,15}$/.test(username)) {
      setMessage("账号格式错误：只填 1–15 位字母、数字或下划线，不要填写网址。"); return;
    }
    if (config) select([...config.accounts, username]);
    setCustom(""); setMessage("账号已加入待保存配置");
  }
  const visible = catalog.filter(item => `${item.name} ${item.username} ${item.company} ${item.reason}`.toLowerCase().includes(search.trim().toLowerCase()));
  const customAccounts = config?.accounts.filter(username => !catalog.some(item => item.username.toLowerCase() === username)) || [];
  return <details>
    <summary>AI 消息监听</summary>
    <p>订阅公司官号、产品号、负责人和创始人的 X 帖子，自动生成原文与中文翻译图片，推送到群聊或私聊。每个账号首次检查只建立基线。</p>
    {config && <form onSubmit={event => {
      event.preventDefault(); setBusy(true); setMessage("");
      void request<View>("/api/tibo-watch", { method: "PUT", body: JSON.stringify(config) })
        .then(view => { apply(view); setMessage("AI 消息监听设置已保存"); })
        .catch(error => setMessage(error instanceof Error ? error.message : "保存失败")).finally(() => setBusy(false));
    }}>
      <div className="extension-grid">
        <label className="extension-toggle"><input type="checkbox" checked={config.enabled} onChange={event => setConfig({ ...config, enabled: event.target.checked })} />启用 AI 消息监听</label>
        <label>消息检查间隔（秒）<input type="number" min={60} max={3600} required value={config.interval_seconds} onChange={event => setConfig({ ...config, interval_seconds: Number(event.target.value) })} /></label>
        <label>推送通知对象<input placeholder="G123456789&P1122334455" required={config.enabled} value={config.targets} onChange={event => setConfig({ ...config, targets: event.target.value })} /></label>
      </div>
      <p>G 后填群号，P 后填私聊 QQ 号，多个对象用 &amp; 连接。默认 120 秒检查；分批抓取，更新速度受 RSS 缓存影响。</p>
      <fieldset className="news-accounts">
        <legend>订阅账号 · 已选 {config.accounts.length} / 100</legend>
        <div className="extension-actions">
          <button type="button" onClick={() => select([...config.accounts, ...catalog.filter(item => item.recommended).map(item => item.username)])}>加入推荐组合</button>
          <button type="button" onClick={() => select([...config.accounts, ...catalog.map(item => item.username)])}>加入全部账号</button>
          <button type="button" onClick={() => select([])}>清空选择</button>
        </div>
        <div className="extension-grid">
          <label>搜索账号或公司<input type="search" value={search} onChange={event => setSearch(event.target.value)} placeholder="OpenAI、Claude、Gemini…" /></label>
          <div className="news-custom-field"><label htmlFor="news-custom-account">自定义 X 账号</label><div className="news-custom-input"><input id="news-custom-account" value={custom} placeholder="@username" onChange={event => setCustom(event.target.value)} onKeyDown={event => { if (event.key === "Enter") { event.preventDefault(); addCustom(); } }} /><button type="button" aria-label="添加自定义 X 账号" onClick={addCustom}><Plus size={16} />添加</button></div></div>
        </div>
        <p className="tibo-watch-status">推荐组合优先覆盖产品与技术消息；创始人和负责人可能发布非 AI 内容。勾选后点击保存生效。</p>
        {categories.map(category => {
          const group = visible.filter(item => item.category === category);
          return group.length > 0 && <details key={category} open={search.trim() ? true : undefined}>
            <summary>{category}账号 · {group.filter(item => config.accounts.includes(item.username.toLowerCase())).length} / {group.length}</summary>
            <div className="extension-actions news-group-actions"><button type="button" onClick={() => select([...config.accounts, ...group.map(item => item.username)])}>选择本类</button><button type="button" onClick={() => select(config.accounts.filter(username => !group.some(item => item.username.toLowerCase() === username)))}>取消本类</button></div>
            <div className="news-account-grid">{group.map(item => <div className="news-account" key={item.username}>
              <label className="extension-toggle"><input type="checkbox" checked={config.accounts.includes(item.username.toLowerCase())} onChange={event => toggle(item.username, event.target.checked)} /><span><strong>{item.name}</strong><small>@{item.username} · {item.company}</small><small>{item.reason}</small></span></label>
              <a href={item.url} target="_blank" rel="noreferrer" aria-label={`查看 @${item.username} 原始账号`}><ExternalLink size={15} /></a>
            </div>)}</div>
          </details>;
        })}
        {customAccounts.length > 0 && <div className="extension-actions news-custom-accounts">{customAccounts.map(username => <button type="button" key={username} onClick={() => toggle(username, false)} aria-label={`移除自定义账号 @${username}`}>@{username}<X size={14} /></button>)}</div>}
      </fieldset>
      <div className="extension-actions">
        <button disabled={busy || (config.enabled && !config.accounts.length)}><Save size={16} />保存 AI 消息监听</button>
        <button type="button" disabled={busy} onClick={() => { setBusy(true); void request<View>("/api/tibo-watch").then(view => {
          apply(view); setMessage("AI 消息监听状态已刷新");
        }).catch(() => setMessage("刷新失败")).finally(() => setBusy(false)); }}><RefreshCw size={16} />刷新监听状态</button>
      </div>
    </form>}
    {message && <p role="status" className="extension-message">{message}</p>}
    {status && <div className="tibo-watch-status">
      <p>监听进度：{status.accounts.filter(item => item.cursor !== null).length} / {status.accounts.length} 个账号已建立基线 · 待推送 {status.pending_count} 条</p>
      <p>最近检查：{time(status.last_checked_at)} · 最近推送：{time(status.last_sent_at)}</p>
      {status.last_error && <p role="alert">{status.last_error}</p>}
      <details><summary>各账号抓取状态</summary><div className="news-account-grid">{status.accounts.map(item => <div className="news-account-state" key={item.username}>
        <strong>@{item.username}</strong><span>{item.last_error ? "获取失败，等待重试" : item.cursor ? "已建立基线" : "等待首次检查"} · 待推送 {item.pending_count}</span><span>最近检查：{time(item.last_checked_at)}</span>
      </div>)}</div></details>
    </div>}
  </details>;
}
