import React, { FormEvent, useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  Activity,
  BarChart3,
  Bot,
  CheckCircle2,
  Clock3,
  KeyRound,
  Send,
  Plus,
  RefreshCcw,
  Server,
  Shield,
  Trash2,
  MessageSquareText,
  Pencil,
  Wifi,
  WifiOff,
  XCircle
} from "lucide-react";
import "./styles.css";

type ApiConfig = {
  id: number;
  name: string;
  target_type: "group" | "private";
  target_id: string;
  target: string;
  base_url: string;
  model_name: string;
  enabled: boolean;
  status: string;
  last_code: string | null;
  last_error: string | null;
  last_checked_at: string | null;
  last_latency_ms: number | null;
  today_availability: number;
  created_at: string;
  updated_at: string;
};

type CheckRecord = {
  id: number;
  checked_at: string;
  status: string;
  code: string | null;
  error: string | null;
  latency_ms: number | null;
  scheduled: boolean;
};

type Admin = {
  id: number;
  qq: string;
  created_at: string;
};

type AppStatus = {
  app_name: string;
  app_timezone: string;
  checker_enabled: boolean;
  onebot_http_configured: boolean;
  onebot_ws_configured: boolean;
  onebot_ws_connected: boolean;
  onebot_ws_last_error: string | null;
};

type ReceivedMessage = {
  id: number;
  received_at: string;
  message_type: "private" | "group";
  user_id: string;
  group_id: string | null;
  message: string;
  triggered: boolean;
  trigger_type: string | null;
  reply_preview: string | null;
};

type SendFailure = {
  id: number;
  sent_at: string;
  action: string;
  target_type: string;
  target_id: string;
  message_preview: string;
  ok: boolean;
  error: string | null;
  status_code: number | null;
  response_payload: Record<string, unknown> | null;
};

type StatusBucket = {
  start_at: string;
  end_at: string;
  state: "unknown" | "ok" | "partial" | "down";
  ok_count: number;
  down_count: number;
  total_count: number;
};

type StatusWindow = {
  key: string;
  label: string;
  bucket_minutes: number;
  buckets: StatusBucket[];
};

type ConfigStatusBars = {
  config_id: number;
  config_name: string;
  target: string;
  model_name: string;
  status: string;
  last_code: string | null;
  success_rate: number;
  windows: StatusWindow[];
};

type ConfigForm = {
  name: string;
  target: string;
  base_url: string;
  api_key: string;
  model_name: string;
};

const emptyForm: ConfigForm = {
  name: "",
  target: "",
  base_url: "",
  api_key: "",
  model_name: ""
};

const defaultTimeZone = "Asia/Shanghai";

async function requestJson<T>(input: RequestInfo, init?: RequestInit): Promise<T> {
  const response = await fetch(input, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {})
    }
  });
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    try {
      const data = await response.json();
      message = data.detail || message;
    } catch {
      // Keep default status text.
    }
    throw new Error(message);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return response.json() as Promise<T>;
}

function parseApiDate(value: string): Date {
  const hasTimezone = /(?:z|[+-]\d{2}:\d{2})$/i.test(value);
  return new Date(hasTimezone || !value.includes("T") ? value : `${value}Z`);
}

function formatTime(value: string | null, timeZone = defaultTimeZone): string {
  if (!value) return "未检查";
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone,
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit"
  }).format(parseApiDate(value));
}

function statusLabel(status: string): string {
  if (status === "ok") return "可用";
  if (status === "down") return "中断";
  return "未知";
}

function statusClass(status: string): string {
  if (status === "ok") return "status ok";
  if (status === "down") return "status down";
  return "status unknown";
}

function statusBucketLabel(state: string): string {
  if (state === "ok") return "200可用";
  if (state === "partial") return "部分可用";
  if (state === "down") return "不可用";
  return "未检查";
}

function App() {
  const [configs, setConfigs] = useState<ApiConfig[]>([]);
  const [history, setHistory] = useState<CheckRecord[]>([]);
  const [admins, setAdmins] = useState<Admin[]>([]);
  const [messages, setMessages] = useState<ReceivedMessage[]>([]);
  const [sendFailures, setSendFailures] = useState<SendFailure[]>([]);
  const [statusBars, setStatusBars] = useState<ConfigStatusBars[]>([]);
  const [status, setStatus] = useState<AppStatus | null>(null);
  const [selectedName, setSelectedName] = useState("");
  const [form, setForm] = useState<ConfigForm>(emptyForm);
  const [adminQq, setAdminQq] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const selectedConfig = useMemo(
    () => configs.find((item) => item.name === selectedName) ?? configs[0],
    [configs, selectedName]
  );

  async function loadAll() {
    const [nextStatus, nextConfigs, nextAdmins, nextMessages, nextSendFailures, nextStatusBars] = await Promise.all([
      requestJson<AppStatus>("/api/status"),
      requestJson<ApiConfig[]>("/api/configs"),
      requestJson<Admin[]>("/api/admins"),
      requestJson<ReceivedMessage[]>("/api/messages/recent"),
      requestJson<SendFailure[]>("/api/sends/recent-failures"),
      requestJson<ConfigStatusBars[]>("/api/status-bars")
    ]);
    setStatus(nextStatus);
    setConfigs(nextConfigs);
    setAdmins(nextAdmins);
    setMessages(nextMessages);
    setSendFailures(nextSendFailures);
    setStatusBars(nextStatusBars);
    if (!selectedName && nextConfigs.length > 0) {
      setSelectedName(nextConfigs[0].name);
    }
  }

  async function loadHistory(name: string | undefined) {
    if (!name) {
      setHistory([]);
      return;
    }
    const rows = await requestJson<CheckRecord[]>(`/api/configs/${encodeURIComponent(name)}/history`);
    setHistory(rows);
  }

  useEffect(() => {
    loadAll().catch((err) => setError(err.message));
    const timer = window.setInterval(() => {
      loadAll().catch((err) => setError(err.message));
    }, 15000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    loadHistory(selectedConfig?.name).catch((err) => setError(err.message));
  }, [selectedConfig?.name]);

  function showNotice(message: string) {
    setNotice(message);
    setError(null);
    window.setTimeout(() => setNotice(null), 3500);
  }

  async function submitConfig(event: FormEvent) {
    event.preventDefault();
    setBusy("add-config");
    try {
      await requestJson<ApiConfig>("/api/configs", {
        method: "POST",
        body: JSON.stringify(form)
      });
      setForm(emptyForm);
      await loadAll();
      showNotice("配置已验证并添加");
    } catch (err) {
      setError(err instanceof Error ? err.message : "添加失败");
    } finally {
      setBusy(null);
    }
  }

  async function toggleConfig(config: ApiConfig) {
    setBusy(`toggle-${config.name}`);
    try {
      await requestJson<ApiConfig>(`/api/configs/${encodeURIComponent(config.name)}`, {
        method: "PATCH",
        body: JSON.stringify({ enabled: !config.enabled })
      });
      await loadAll();
      showNotice(config.enabled ? "配置已停用" : "配置已启用");
    } catch (err) {
      setError(err instanceof Error ? err.message : "更新失败");
    } finally {
      setBusy(null);
    }
  }

  async function editConfigName(config: ApiConfig) {
    const nextName = window.prompt("编辑 API 名称", config.name)?.trim();
    if (nextName == null || nextName === config.name) return;
    if (!nextName) {
      setError("API 名称不能为空");
      return;
    }
    setBusy(`rename-${config.name}`);
    try {
      const updated = await requestJson<ApiConfig>(`/api/configs/${encodeURIComponent(config.name)}`, {
        method: "PATCH",
        body: JSON.stringify({ name: nextName })
      });
      await loadAll();
      setSelectedName(updated.name);
      await loadHistory(updated.name);
      showNotice("API 名称已更新");
    } catch (err) {
      setError(err instanceof Error ? err.message : "更新 API 名称失败");
    } finally {
      setBusy(null);
    }
  }

  async function editModelName(config: ApiConfig) {
    const nextModelName = window.prompt("编辑模型名称", config.model_name)?.trim();
    if (nextModelName == null || nextModelName === config.model_name) return;
    if (!nextModelName) {
      setError("模型名称不能为空");
      return;
    }
    setBusy(`model-${config.name}`);
    try {
      await requestJson<ApiConfig>(`/api/configs/${encodeURIComponent(config.name)}`, {
        method: "PATCH",
        body: JSON.stringify({ model_name: nextModelName })
      });
      await loadAll();
      showNotice("模型名称已更新");
    } catch (err) {
      setError(err instanceof Error ? err.message : "更新模型名称失败");
    } finally {
      setBusy(null);
    }
  }

  async function deleteConfig(config: ApiConfig) {
    if (!window.confirm(`删除配置 ${config.name}？`)) return;
    setBusy(`delete-${config.name}`);
    try {
      await requestJson<void>(`/api/configs/${encodeURIComponent(config.name)}`, { method: "DELETE" });
      setSelectedName("");
      await loadAll();
      showNotice("配置已删除");
    } catch (err) {
      setError(err instanceof Error ? err.message : "删除失败");
    } finally {
      setBusy(null);
    }
  }

  async function manualCheck(config: ApiConfig) {
    setBusy(`check-${config.name}`);
    try {
      const result = await requestJson<{ ok: boolean; code: string }>(
        `/api/configs/${encodeURIComponent(config.name)}/check`,
        { method: "POST" }
      );
      await loadAll();
      await loadHistory(config.name);
      showNotice(`手动检查完成：${result.ok ? "可用" : "不可用"} ${result.code}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "检查失败");
    } finally {
      setBusy(null);
    }
  }

  async function addAdmin(event: FormEvent) {
    event.preventDefault();
    setBusy("add-admin");
    try {
      await requestJson<Admin>("/api/admins", {
        method: "POST",
        body: JSON.stringify({ qq: adminQq })
      });
      setAdminQq("");
      await loadAll();
      showNotice("管理员已添加");
    } catch (err) {
      setError(err instanceof Error ? err.message : "添加管理员失败");
    } finally {
      setBusy(null);
    }
  }

  async function deleteAdmin(admin: Admin) {
    setBusy(`admin-${admin.qq}`);
    try {
      await requestJson<void>(`/api/admins/${encodeURIComponent(admin.qq)}`, { method: "DELETE" });
      await loadAll();
      showNotice("管理员已移除");
    } catch (err) {
      setError(err instanceof Error ? err.message : "移除管理员失败");
    } finally {
      setBusy(null);
    }
  }

  const healthyCount = configs.filter((item) => item.status === "ok").length;
  const downCount = configs.filter((item) => item.status === "down").length;
  const displayTimeZone = status?.app_timezone || defaultTimeZone;

  return (
    <main className="app-shell">
      <header className="topbar">
        <div>
          <p className="eyebrow">OneBot API Monitor</p>
          <h1>APIMonitorBot</h1>
        </div>
        <button className="icon-button" onClick={() => loadAll().catch((err) => setError(err.message))} aria-label="刷新">
          <RefreshCcw size={18} />
        </button>
      </header>

      <section className="metrics" aria-label="系统状态">
        <Metric icon={<Server />} label="API 配置" value={configs.length.toString()} tone="neutral" />
        <Metric icon={<CheckCircle2 />} label="可用" value={healthyCount.toString()} tone="ok" />
        <Metric icon={<XCircle />} label="中断" value={downCount.toString()} tone="down" />
        <Metric
          icon={status?.onebot_ws_connected ? <Wifi /> : <WifiOff />}
          label="OneBot WS"
          value={status?.onebot_ws_connected ? "已连接" : status?.onebot_ws_configured ? "未连接" : "未配置"}
          tone={status?.onebot_ws_connected ? "ok" : "warn"}
        />
      </section>

      {(notice || error) && (
        <div className={error ? "toast error" : "toast"} role="status" aria-live="polite">
          {error || notice}
        </div>
      )}

      <section className="workspace">
        <div className="panel main-panel">
          <div className="panel-heading">
            <div>
              <h2>监控配置</h2>
              <p>定时巡检只统计每分钟任务，手动检查不会改变告警状态。</p>
            </div>
          </div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>名称</th>
                  <th>目标</th>
                  <th>模型</th>
                  <th>状态</th>
                  <th>最近请求成功率</th>
                  <th>最近检查</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {configs.length === 0 ? (
                  <tr>
                    <td colSpan={7} className="empty">还没有配置。添加一个 API 后会开始监控。</td>
                  </tr>
                ) : (
                  configs.map((config) => (
                    <tr key={config.id} className={selectedConfig?.name === config.name ? "selected" : ""}>
                      <td>
                        <div className="editable-cell">
                          <button className="link-button" onClick={() => setSelectedName(config.name)}>
                            {config.name}
                          </button>
                          <button
                            className="icon-button tiny edit-button"
                            onClick={() => editConfigName(config)}
                            disabled={busy === `rename-${config.name}`}
                            aria-label={`编辑 API 名称 ${config.name}`}
                            title="编辑 API 名称"
                          >
                            <Pencil size={14} />
                          </button>
                        </div>
                        <div className="subtle">{config.base_url}</div>
                      </td>
                      <td>{config.target}</td>
                      <td>
                        <div className="editable-cell">
                          <span className="model-name">{config.model_name}</span>
                          <button
                            className="icon-button tiny edit-button"
                            onClick={() => editModelName(config)}
                            disabled={busy === `model-${config.name}`}
                            aria-label={`编辑模型名称 ${config.model_name}`}
                            title="编辑模型名称"
                          >
                            <Pencil size={14} />
                          </button>
                        </div>
                      </td>
                      <td>
                        <span className={statusClass(config.status)}>{statusLabel(config.status)}</span>
                        {config.last_code && <span className="code">{config.last_code}</span>}
                      </td>
                      <td>{config.today_availability.toFixed(1)}%</td>
                      <td>{formatTime(config.last_checked_at, displayTimeZone)}</td>
                      <td className="actions">
                        <button onClick={() => manualCheck(config)} disabled={busy === `check-${config.name}`}>
                          <Activity size={16} />
                          检查
                        </button>
                        <button onClick={() => toggleConfig(config)} disabled={busy === `toggle-${config.name}`}>
                          {config.enabled ? "停用" : "启用"}
                        </button>
                        <button className="danger" onClick={() => deleteConfig(config)} disabled={busy === `delete-${config.name}`}>
                          <Trash2 size={16} />
                        </button>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>

        <aside className="side-stack">
          <form className="panel form-panel" onSubmit={submitConfig}>
            <div className="panel-heading compact">
              <h2>添加 API</h2>
              <Plus size={18} />
            </div>
            <Field label="配置名称" value={form.name} onChange={(name) => setForm({ ...form, name })} required />
            <Field label="报告目标" value={form.target} placeholder="G123456789 或 P2087900785" onChange={(target) => setForm({ ...form, target })} required />
            <Field label="BaseURL" value={form.base_url} placeholder="https://example.com/v1" onChange={(base_url) => setForm({ ...form, base_url })} required />
            <Field label="APIKey" value={form.api_key} type="password" onChange={(api_key) => setForm({ ...form, api_key })} required />
            <Field label="模型名称" value={form.model_name} placeholder="gpt-4.1-mini" onChange={(model_name) => setForm({ ...form, model_name })} required />
            <button className="primary" disabled={busy === "add-config"} type="submit">
              <KeyRound size={16} />
              验证并添加
            </button>
          </form>

          <div className="panel">
            <div className="panel-heading compact">
              <h2>管理员</h2>
              <Shield size={18} />
            </div>
            <form className="inline-form" onSubmit={addAdmin}>
              <input value={adminQq} onChange={(event) => setAdminQq(event.target.value)} placeholder="QQ号" aria-label="管理员QQ号" />
              <button disabled={busy === "add-admin"} type="submit">添加</button>
            </form>
            <div className="admin-list">
              {admins.map((admin) => (
                <div className="admin-row" key={admin.id}>
                  <span>{admin.qq}</span>
                  <button className="icon-button small" onClick={() => deleteAdmin(admin)} aria-label={`移除管理员 ${admin.qq}`}>
                    <Trash2 size={15} />
                  </button>
                </div>
              ))}
            </div>
          </div>
        </aside>
      </section>

      <section className="panel status-bars-panel">
        <div className="panel-heading">
          <div>
            <h2>状态条图</h2>
            <p>最近 30 分钟、5 小时、24 小时的定时巡检状态。</p>
          </div>
          <BarChart3 size={18} />
        </div>
        <div className="status-bars-list">
          {statusBars.length === 0 ? (
            <div className="empty">暂无状态条数据。</div>
          ) : (
            statusBars.map((item) => (
              <div className="status-bars-card" key={item.config_id}>
                <div className="status-bars-title">
                  <div>
                    <strong>{item.config_name}</strong>
                    <span>{item.target} · {item.model_name}</span>
                  </div>
                  <span className={statusClass(item.status)}>{statusLabel(item.status)}</span>
                </div>
                {item.windows.map((windowItem) => (
                  <div className="status-window-row" key={`${item.config_id}-${windowItem.key}`}>
                    <span className="status-window-label">{windowItem.label}</span>
                    <div className="status-strip" aria-label={`${item.config_name} ${windowItem.label} 状态条`}>
                      {windowItem.buckets.map((bucket, index) => (
                        <span
                          className={`status-segment ${bucket.state}`}
                          key={`${windowItem.key}-${index}`}
                          title={`${formatTime(bucket.start_at, displayTimeZone)} - ${formatTime(bucket.end_at, displayTimeZone)} · ${statusBucketLabel(bucket.state)} · ${bucket.total_count} 次`}
                        />
                      ))}
                    </div>
                    <span className="status-window-meta">{windowItem.bucket_minutes} 分/格</span>
                  </div>
                ))}
              </div>
            ))
          )}
        </div>
        <div className="status-legend" aria-label="状态条图例">
          <span><i className="status-dot unknown" />未检查</span>
          <span><i className="status-dot ok" />200可用</span>
          <span><i className="status-dot partial" />部分可用</span>
          <span><i className="status-dot down" />不可用</span>
        </div>
      </section>

      <section className="panel send-failures-panel">
        <div className="panel-heading">
          <div>
            <h2>最近发送失败</h2>
            <p>最近 10 条 OneBot 发送失败记录，用于排查 HTTP API、token 和群权限问题。</p>
          </div>
          <Send size={18} />
        </div>
        <div className="send-failure-list">
          {sendFailures.length === 0 ? (
            <div className="empty">暂无发送失败。</div>
          ) : (
            sendFailures.map((item) => (
              <div className="send-failure-row" key={item.id}>
                <span className="message-time">{formatTime(item.sent_at, displayTimeZone)}</span>
                <span className="message-source">
                  {item.action} · {item.target_type}:{item.target_id}
                </span>
                <span className="send-error">
                  {item.status_code ? `HTTP ${item.status_code} · ` : ""}
                  {item.error || "发送失败"}
                </span>
                <span className="message-text">{JSON.stringify(item.response_payload || {})}</span>
              </div>
            ))
          )}
        </div>
      </section>

      <section className="panel messages-panel">
        <div className="panel-heading">
          <div>
            <h2>最近接收消息</h2>
            <p>最近 10 条 OneBot 消息，触发机器人动作的记录会高亮。</p>
          </div>
          <MessageSquareText size={18} />
        </div>
        <div className="message-list">
          {messages.length === 0 ? (
            <div className="empty">暂无接收消息。</div>
          ) : (
            messages.map((message) => (
              <div className={message.triggered ? "message-row triggered" : "message-row"} key={message.id}>
                <span className="message-time">{formatTime(message.received_at, displayTimeZone)}</span>
                <span className="message-source">
                  {message.message_type === "group" ? `群 ${message.group_id}` : "私聊"} · {message.user_id}
                </span>
                <span className="message-text">{message.message}</span>
                <span className="message-trigger">{message.triggered ? message.trigger_type || "已触发" : "未触发"}</span>
              </div>
            ))
          )}
        </div>
      </section>

      <section className="panel history-panel">
        <div className="panel-heading">
          <div>
            <h2>巡检历史</h2>
            <p>{selectedConfig ? `${selectedConfig.name} 最近 200 条记录` : "选择一个配置查看记录"}</p>
          </div>
          <Clock3 size={18} />
        </div>
        <div className="history-list">
          {history.length === 0 ? (
            <div className="empty">暂无历史记录。</div>
          ) : (
            history.map((row) => (
              <div className="history-row" key={row.id}>
                <span className={statusClass(row.status)}>{statusLabel(row.status)}</span>
                <span>{formatTime(row.checked_at, displayTimeZone)}</span>
                <span>{row.code || "-"}</span>
                <span>{row.latency_ms == null ? "-" : `${row.latency_ms}ms`}</span>
                <span>{row.scheduled ? "定时" : "手动"}</span>
                <span className="history-error">{row.error || ""}</span>
              </div>
            ))
          )}
        </div>
      </section>

      <footer className="footer">
        <Bot size={16} />
        HTTP {status?.onebot_http_configured ? "已配置" : "未配置"} · WebSocket {status?.onebot_ws_configured ? "已配置" : "未配置"}
        {` · 时区 ${displayTimeZone}`}
        {status?.onebot_ws_last_error ? ` · ${status.onebot_ws_last_error}` : ""}
      </footer>
    </main>
  );
}

function Metric({ icon, label, value, tone }: { icon: React.ReactNode; label: string; value: string; tone: "neutral" | "ok" | "down" | "warn" }) {
  return (
    <div className={`metric ${tone}`}>
      <div className="metric-icon" aria-hidden="true">{icon}</div>
      <div>
        <span>{label}</span>
        <strong>{value}</strong>
      </div>
    </div>
  );
}

function Field({
  label,
  value,
  onChange,
  placeholder,
  type = "text",
  required = false
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  type?: string;
  required?: boolean;
}) {
  return (
    <label className="field">
      <span>{label}</span>
      <input
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
        type={type}
        required={required}
        autoComplete={type === "password" ? "off" : undefined}
      />
    </label>
  );
}

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
