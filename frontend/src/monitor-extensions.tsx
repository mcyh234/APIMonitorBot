import React, { useEffect, useState } from "react";
import { Save, RefreshCw, ExternalLink, Eye, X, PlugZap } from "lucide-react";
import "./monitor-extensions.css";

type RequestJSON = <T>(url: string, init?: RequestInit) => Promise<T>;
type IntelSettings = {
  enabled: boolean; group_ids: string[]; keywords: string[]; context_before_messages: number;
  context_after_messages: number; settle_seconds: number; min_score: number; dedup_minutes: number; llm_enabled: boolean;
};
type LLM = { base_url: string; model: string; timeout_seconds: number; api_key_configured: boolean };
type Rule = { config_id: number; config_name?: string; platform: string; group_key: string; group_name?: string; visible: boolean };
type PublicSettings = { enabled: boolean; grouping_mode: string; rules: Rule[]; candidates: Rule[] };
type Finding = { id: number; group_id: string; score: number; status: string; created_at: string };
type Detail = { summary: string; urls: string[]; context: { user_id: string; message: string }[]; analysis?: { summary: string } };
type Advanced = {
  checker_enabled: boolean; check_interval_seconds: number; check_retry_delay_seconds: number; request_timeout_seconds: number;
  api_probe_model_fallback_enabled: boolean; api_probe_fallback_models: string; outage_repeat_checks: number;
  recovery_confirm_checks: number; hide_status_targets: boolean;
};

const save = (data: unknown): RequestInit => ({ method: "PUT", body: JSON.stringify(data) });
const list = (text: string) => text.split(/[,，\n]+/).map(v => v.trim()).filter(Boolean);

export function MonitorExtensions({ request }: { request: RequestJSON }) {
  const [intel, setIntel] = useState<IntelSettings | null>(null);
  const [groups, setGroups] = useState("");
  const [keywords, setKeywords] = useState("");
  const [llm, setLLM] = useState<LLM | null>(null);
  const [key, setKey] = useState("");
  const [clearKey, setClearKey] = useState(false);
  const [pub, setPub] = useState<PublicSettings | null>(null);
  const [advanced, setAdvanced] = useState<Advanced | null>(null);
  const [findings, setFindings] = useState<Finding[]>([]);
  const [detail, setDetail] = useState<Detail | null>(null);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!detail) return;
    const previous = document.activeElement as HTMLElement | null;
    const handler = (event: KeyboardEvent) => {
      if (event.key === "Escape") setDetail(null);
      if (event.key === "Tab") {
        const nodes = Array.from(document.querySelectorAll<HTMLElement>(".extension-detail button, .extension-detail a"));
        const first = nodes[0], last = nodes[nodes.length - 1];
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
        if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
      }
    };
    document.addEventListener("keydown", handler);
    return () => { document.removeEventListener("keydown", handler); previous?.focus(); };
  }, [detail]);

  async function action(work: () => Promise<void>) {
    setBusy(true); setMessage("");
    try { await work(); } catch (error) { setMessage(error instanceof Error ? error.message : "操作失败"); }
    finally { setBusy(false); }
  }
  async function load() {
    const [i, l, p, a, f] = await Promise.all([
      request<IntelSettings>("/api/intelligence/settings"), request<LLM>("/api/intelligence/llm"),
      request<PublicSettings>("/api/public-settings"), request<Advanced>("/api/monitoring/advanced"),
      request<Finding[]>("/api/intelligence/findings")
    ]);
    setIntel(i); setGroups(i.group_ids.join(", ")); setKeywords(i.keywords.join(", "));
    setLLM(l); setPub(p); setAdvanced(a); setFindings(f);
  }
  useEffect(() => { void action(load); }, []);
  useEffect(() => {
    let active = true;
    const timer = window.setInterval(() => {
      void request<Finding[]>("/api/intelligence/findings").then(rows => { if (active) setFindings(rows); }).catch(() => {});
    }, 30000);
    return () => { active = false; window.clearInterval(timer); };
  }, [request]);
  return <section className="monitor-extensions">
    <div className="extension-heading"><h2>监控管理</h2><button title="刷新" aria-label="刷新监控管理" disabled={busy} onClick={() => void action(load)}><RefreshCw size={17} /></button></div>
    {message && <p role="status" className="extension-message">{message}</p>}
    {!intel && <p>{busy ? "加载中…" : "监控管理暂不可用"}</p>}
    {advanced && <details>
      <summary>高级巡检设置</summary>
      <form onSubmit={event => { event.preventDefault(); void action(async () => { setAdvanced(await request("/api/monitoring/advanced", save(advanced))); setMessage("巡检设置已保存"); }); }}>
        <div className="extension-grid">
          <Toggle label="启用 API 巡检" value={advanced.checker_enabled} onChange={checker_enabled => setAdvanced({ ...advanced, checker_enabled })} />
          <Toggle label="隐藏状态图通知对象" value={advanced.hide_status_targets} onChange={hide_status_targets => setAdvanced({ ...advanced, hide_status_targets })} />
          {([["check_interval_seconds", "巡检间隔（秒）", 10, 86400], ["check_retry_delay_seconds", "重试等待（秒）", 0, 120],
             ["request_timeout_seconds", "请求超时（秒）", 1, 120], ["outage_repeat_checks", "故障追报检查次数", 2, 1000],
             ["recovery_confirm_checks", "恢复确认次数", 1, 100]] as const).map(([name, label, min, max]) =>
            <NumberField key={name} label={label} value={advanced[name]} min={min} max={max} onChange={value => setAdvanced({ ...advanced, [name]: value })} />)}
          <Toggle label="启用模型回退" value={advanced.api_probe_model_fallback_enabled} onChange={api_probe_model_fallback_enabled => setAdvanced({ ...advanced, api_probe_model_fallback_enabled })} />
          <label>回退模型<input value={advanced.api_probe_fallback_models} onChange={e => setAdvanced({ ...advanced, api_probe_fallback_models: e.target.value })} /></label>
        </div>
        <button disabled={busy}><Save size={16} />保存巡检设置</button>
      </form>
    </details>}
    {intel && <details>
      <summary>群聊情报</summary>
      <form onSubmit={e => { e.preventDefault(); void action(async () => {
        const updated = await request<IntelSettings>("/api/intelligence/settings", save({ ...intel, group_ids: list(groups), keywords: list(keywords) }));
        setIntel(updated); setMessage("群聊情报设置已保存");
      }); }}>
        <div className="extension-grid">
          <Toggle label="启用群聊情报" value={intel.enabled} onChange={enabled => setIntel({ ...intel, enabled })} />
          <Toggle label="使用 LLM 分析" value={intel.llm_enabled} onChange={llm_enabled => setIntel({ ...intel, llm_enabled })} />
          <label>监听群号<textarea aria-label="监听群号" value={groups} onChange={e => setGroups(e.target.value)} /></label>
          <label>关键词<textarea aria-label="关键词" value={keywords} onChange={e => setKeywords(e.target.value)} /></label>
          {([["context_before_messages", "前文条数", 0, 50], ["context_after_messages", "后文条数", 0, 50],
             ["settle_seconds", "收集等待（秒）", 0, 120], ["min_score", "最低评分", 0, 100],
             ["dedup_minutes", "去重窗口（分钟）", 1, 10080]] as const).map(([name, label, min, max]) =>
              <NumberField key={name} label={label} value={intel[name]} min={min} max={max} onChange={value => setIntel({ ...intel, [name]: value })} />)}
        </div>
        <button disabled={busy}><Save size={16} />保存情报设置</button>
      </form>
      {llm && <form onSubmit={e => { e.preventDefault(); void action(async () => {
        setLLM(await request("/api/intelligence/llm", save({ ...llm, api_key: key || null, clear_api_key: clearKey })));
        setKey(""); setClearKey(false); setMessage("LLM 设置已保存");
      }); }}>
        <h3>分析模型</h3>
        <div className="extension-grid">
          <label>BaseURL<input type="url" value={llm.base_url} onChange={e => setLLM({ ...llm, base_url: e.target.value })} /></label>
          <label>模型<input value={llm.model} onChange={e => setLLM({ ...llm, model: e.target.value })} /></label>
          <label>API Key {llm.api_key_configured ? "（已配置）" : "（未配置）"}<input autoComplete="new-password" type="password" value={key} onChange={e => setKey(e.target.value)} /></label>
          <NumberField label="LLM 超时（秒）" min={1} max={120} value={llm.timeout_seconds} onChange={timeout_seconds => setLLM({ ...llm, timeout_seconds })} />
          <Toggle label="清除已保存密钥" value={clearKey} onChange={setClearKey} />
        </div>
        <div className="extension-actions">
          <button disabled={busy}><Save size={16} />保存模型</button>
          <button type="button" disabled={busy} onClick={() => void action(async () => { await request("/api/intelligence/llm/test", { method: "POST" }); setMessage("已保存的模型连接测试通过"); })}><PlugZap size={16} />测试已保存配置</button>
        </div>
      </form>}
      <h3>最近情报</h3>
      {findings.length === 0 ? <p>暂无情报</p> : <div className="extension-table"><table><thead><tr><th>时间</th><th>群号</th><th>评分</th><th>状态</th><th /></tr></thead><tbody>
        {findings.map(f => <tr key={f.id}><td>{new Date(f.created_at).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai" })}</td><td>{f.group_id}</td><td>{f.score}</td><td>{{ reported: "已报告", held: "待复核", delivery_failed: "发送失败" }[f.status] || f.status}</td>
          <td><button title="查看情报详情" aria-label="查看情报详情" disabled={busy} onClick={() => void action(async () => setDetail(await request(`/api/intelligence/findings/${f.id}`)))}><Eye size={16} /></button></td></tr>)}
      </tbody></table></div>}
    </details>}
    {pub && <details>
      <summary>公开状态页</summary>
      <form onSubmit={e => { e.preventDefault(); void action(async () => {
        const candidates = new Set(pub.candidates.map(ruleKey));
        setPub(await request("/api/public-settings", save({ ...pub, rules: [...pub.rules.filter(r => !candidates.has(ruleKey(r))), ...pub.candidates] })));
        setMessage("公开状态页设置已保存");
      }); }}>
        <div className="extension-grid">
          <Toggle label="启用公开状态页" value={pub.enabled} onChange={enabled => setPub({ ...pub, enabled })} />
          <label>分组方式<select value={pub.grouping_mode} onChange={e => setPub({ ...pub, grouping_mode: e.target.value })}><option value="upstream">按上游</option><option value="group">按分组</option></select></label>
        </div>
        <div className="public-rules">{pub.candidates.map((rule, i) => <Toggle key={ruleKey(rule)} label={`${rule.config_name} / ${rule.platform} / ${rule.group_name}`} value={rule.visible} onChange={visible => setPub({ ...pub, candidates: pub.candidates.map((r, j) => i === j ? { ...r, visible } : r) })} />)}</div>
        <div className="extension-actions"><button disabled={busy}><Save size={16} />保存公开设置</button><a href="/status" target="_blank" rel="noreferrer"><ExternalLink size={16} />打开公开页</a></div>
      </form>
    </details>}
    {detail && <div className="extension-overlay" role="presentation" onClick={() => setDetail(null)}><section role="dialog" aria-modal="true" aria-label="情报详情" className="extension-detail" onClick={e => e.stopPropagation()}>
      <button autoFocus aria-label="关闭详情" title="关闭" onClick={() => setDetail(null)}><X size={18} /></button>
      <h2>情报详情</h2><p>{detail.analysis?.summary || detail.summary}</p>
      {detail.urls.filter(url => /^https?:\/\//.test(url)).map(url => <p key={url}><a href={url} target="_blank" rel="noreferrer">{url}</a></p>)}
      <h3>上下文</h3>{detail.context.map((item, i) => <p key={i}><strong>{item.user_id}</strong> {item.message}</p>)}
    </section></div>}
  </section>;
}
function ruleKey(rule: Rule) { return JSON.stringify([rule.config_id, rule.platform, rule.group_key]); }
function Toggle({ label, value, onChange }: { label: string; value: boolean; onChange: (v: boolean) => void }) {
  return <label className="extension-toggle"><input type="checkbox" checked={value} onChange={e => onChange(e.target.checked)} />{label}</label>;
}
function NumberField({ label, value, min, max, onChange }: { label: string; value: number; min: number; max: number; onChange: (v: number) => void }) {
  return <label>{label}<input required type="number" min={min} max={max} value={value} onChange={e => onChange(e.target.valueAsNumber)} /></label>;
}

type PublicRow = { group_name: string; platform: string; ratio: number; status: string; last_success_at: string;
  prices: { model_name: string; input_price: number; output_price: number; cache_write_price: number; cache_read_price: number }[] };
export function PublicStatusPage() {
  const [sections, setSections] = useState<{ title: string; rows: PublicRow[] }[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    let alive = true;
    async function load() {
      try {
        const response = await fetch("/api/public-status");
        if (!response.ok) throw new Error(response.status === 404 ? "公开状态页未启用" : "状态暂不可用");
        const data = await response.json();
        if (alive) { setSections(data.sections); setError(""); }
      } catch (e) { if (alive) setError(e instanceof Error ? e.message : "加载失败"); }
      finally { if (alive) setLoading(false); }
    }
    void load(); const interval = window.setInterval(load, 60000);
    return () => { alive = false; window.clearInterval(interval); };
  }, []);
  return <main className="public-monitor"><h1>API Monitor 状态</h1>
    {loading && <p>加载中…</p>}{error && <p role="alert">{error}</p>}
    {!loading && !error && sections.length === 0 && <p>暂无公开分组</p>}
    {!error && sections.map((section, i) => <section key={i}><h2>{section.title || `上游 ${i + 1}`}</h2>
      <div className="extension-table"><table><thead><tr><th>平台 / 分组</th><th>状态</th><th>倍率</th><th>最近成功</th><th>模型单价（CNY / MTok）</th></tr></thead><tbody>
        {section.rows.map((r, j) => <tr key={j}><td>{r.platform} / {r.group_name}</td><td data-label="状态"><span className={`public-state ${r.status}`}>{{ normal: "正常", error: "异常", stale: "过期", paused: "暂停" }[r.status] || "未知"}</span></td><td data-label="倍率">{r.ratio}x</td><td data-label="最近成功">{r.last_success_at ? new Date(r.last_success_at).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai" }) : "—"}</td>
        <td data-label="模型单价（CNY / MTok）">{r.prices.length ? r.prices.map(p => <div key={p.model_name}>{p.model_name} · 输入 {(p.input_price * 1e6 * r.ratio).toFixed(3)} / 输出 {(p.output_price * 1e6 * r.ratio).toFixed(3)} / 缓存写 {(p.cache_write_price * 1e6 * r.ratio).toFixed(3)} / 缓存读 {(p.cache_read_price * 1e6 * r.ratio).toFixed(3)}</div>) : "暂无模型价格"}</td></tr>)}
      </tbody></table></div></section>)}
  </main>;
}

export type LatencyPoint = { at: string; latency_ms: number; model_switched: boolean; code?: string };
export type ChartWindow = { bucket_minutes: number; buckets: { start_at: string; end_at: string; total_count: number; timeout_count?: number }[]; latency_points?: LatencyPoint[] };
export function showTimeout(window: ChartWindow) {
  if (window.bucket_minutes <= 1) return true;
  const count = window.buckets.reduce((sum, b) => sum + (b.timeout_count || 0), 0);
  const total = window.buckets.reduce((sum, b) => sum + b.total_count, count);
  return total > 0 && count / total > .4;
}
function median(values: number[]) { const sorted = [...values].sort((a, b) => a - b); const m = Math.floor(sorted.length / 2); return sorted.length % 2 ? sorted[m] : (sorted[m - 1] + sorted[m]) / 2; }
export function LatencyChart({ window: w }: { window: ChartWindow }) {
  const raw = w.latency_points || [];
  let points = raw;
  if (w.bucket_minutes > 1) points = w.buckets.flatMap(b => {
    const start = Date.parse(b.start_at), end = Date.parse(b.end_at);
    const values = raw.filter(p => Date.parse(p.at) >= start && Date.parse(p.at) < end);
    return values.length ? [{ at: new Date(start + (end - start) / 2).toISOString(), latency_ms: median(values.map(p => p.latency_ms)), model_switched: values.some(p => p.model_switched) }] : [];
  });
  if (!points.length || !w.buckets.length) return null;
  const start = Date.parse(w.buckets[0].start_at), end = Date.parse(w.buckets[w.buckets.length - 1].end_at);
  const baseline = median(raw.map(p => Math.max(0, p.latency_ms)));
  const mad = median(raw.map(p => Math.abs(p.latency_ms - baseline)));
  const threshold = Math.max(baseline * 1.8, baseline + 4 * mad);
  const outlier = (p: LatencyPoint) => raw.length >= 5 && p.latency_ms > threshold;
  const normal = raw.filter(p => !outlier(p));
  const max = Math.max(1, ...(normal.length ? normal : raw).map(p => p.latency_ms)) * 1.1;
  const position = (p: LatencyPoint) => [4 + (Date.parse(p.at) - start) / Math.max(1, end - start) * 692, 38 - Math.min(Math.max(0, p.latency_ms), max) / max * 32];
  const coords = points.map(position);
  return <svg className="monitor-latency" viewBox="0 0 760 44" preserveAspectRatio="none" role="img" aria-label="响应延迟曲线">
    <polyline points={coords.map(p => p.join(",")).join(" ")} fill="none" stroke="currentColor" strokeWidth="1.5" />
    {raw.filter(p => p.model_switched || outlier(p) || raw.length === 1).map((p, i) => <circle key={`${p.at}-${i}`} cx={position(p)[0]} cy={position(p)[1]} r={2} fill={p.model_switched || outlier(p) ? "#ef4444" : "currentColor"}><title>{p.latency_ms} ms{p.model_switched ? " · 模型切换" : ""}</title></circle>)}
    <text x="710" y="22" fill="currentColor" fontSize="11">{(max / 1000).toFixed(1)}s</text>
  </svg>;
}

export function ProbeSettings({ config, request, onSaved }: {
  config: { name: string; base_url: string; protocol?: string; verify_tls?: boolean };
  request: RequestJSON; onSaved: () => Promise<void>;
}) {
  const [protocol, setProtocol] = useState(config.protocol || "auto");
  const [tls, setTLS] = useState(!!config.verify_tls);
  const [base, setBase] = useState(config.base_url);
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => { setProtocol(config.protocol || "auto"); setTLS(!!config.verify_tls); setBase(config.base_url); }, [config.protocol, config.verify_tls, config.base_url]);
  return <details className="probe-settings"><summary>连接设置</summary>
    <form onSubmit={e => { e.preventDefault(); setBusy(true); setError("");
      void request(`/api/configs/${encodeURIComponent(config.name)}`, { method: "PATCH", body: JSON.stringify({
        protocol, verify_tls: tls, base_url: base, ...(key ? { api_key: key } : {})
      }) }).then(async () => { setKey(""); await onSaved(); }).catch(e => setError(String(e.message || e))).finally(() => setBusy(false));
    }}>
      <label>协议<select value={protocol} onChange={e => setProtocol(e.target.value)}><option value="auto">自动识别</option><option value="openai">OpenAI</option><option value="anthropic">Anthropic / Claude</option></select></label>
      <label>BaseURL<input type="url" required value={base} onChange={e => setBase(e.target.value)} /></label>
      <label>更换 API Key<input type="password" autoComplete="new-password" value={key} onChange={e => setKey(e.target.value)} /></label>
      <Toggle label="校验 TLS 证书" value={tls} onChange={setTLS} />
      <button disabled={busy}><Save size={14} />保存</button>
      {error && <p role="alert">{error}</p>}
    </form>
  </details>;
}
