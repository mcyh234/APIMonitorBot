import React, { FormEvent, useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  Activity,
  BarChart3,
  Bot,
  BadgePercent,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Circle,
  ClipboardList,
  Cable,
  KeyRound,
  LockKeyhole,
  LogIn,
  Send,
  Plus,
  RefreshCcw,
  Server,
  Settings2,
  Shield,
  Trash2,
  MessageSquareText,
  Pencil,
  WandSparkles,
  TrendingDown,
  TrendingUp,
  Wifi,
  WifiOff,
  XCircle
} from "lucide-react";
import "./styles.css";

type ApiConfig = {
  id: number;
  name: string;
  target_type: "group" | "private" | "multi";
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

type AuthStatus = {
  configured: boolean;
  authenticated: boolean;
};

type OneBotSettings = {
  ws_url: string;
  access_token_configured: boolean;
  access_token_preview: string | null;
  ws_token_in_query: boolean;
  connected: boolean;
  last_error: string | null;
};

type CommandSetting = {
  command: string;
  label: string;
  description: string;
  enabled: boolean;
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

type Sub2RateHistoryPoint = {
  recorded_at: string;
  rate_multiplier: number;
};

type Sub2Rate = {
  platform: string;
  group_key: string;
  group_name: string;
  rate_multiplier: number;
  previous_rate: number | null;
  change_percent: number | null;
  last_seen_at: string;
  history: Sub2RateHistoryPoint[];
};

type Sub2PriceBoard = {
  config_id: number;
  name: string;
  target_type: "group" | "private" | "multi";
  target_id: string;
  target: string;
  base_url: string;
  enabled: boolean;
  last_checked_at: string | null;
  last_error: string | null;
  rates: Sub2Rate[];
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

const HISTORY_LIMIT = 60;
const defaultTimeZone = "Asia/Shanghai";
const authTokenKey = "apimonitorbot.webui.token";

function getAuthToken(): string {
  return window.localStorage.getItem(authTokenKey) || "";
}

function setAuthToken(token: string) {
  window.localStorage.setItem(authTokenKey, token);
}

function clearAuthToken() {
  window.localStorage.removeItem(authTokenKey);
}

async function requestJson<T>(input: RequestInfo, init?: RequestInit): Promise<T> {
  const token = getAuthToken();
  const response = await fetch(input, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
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

function platformLabel(platform: string): string {
  const clean = platform.trim().toLowerCase();
  if (clean === "openai") return "OpenAI";
  if (clean === "anthropic") return "Anthropic";
  return platform.trim() || "Unknown";
}

function platformClass(platform: string): string {
  const clean = platform.trim().toLowerCase();
  if (clean === "openai") return "openai";
  if (clean === "anthropic") return "anthropic";
  return "other";
}

function formatRate(value: number): string {
  return `${Number(value.toPrecision(6)).toString()}x`;
}

function formatPercent(value: number | null): string {
  if (value == null || !Number.isFinite(value)) return "基准";
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(1)}%`;
}

function trendClass(value: number | null): "up" | "down" | "flat" {
  if (value == null || Math.abs(value) < 0.0000001) return "flat";
  return value > 0 ? "up" : "down";
}

function groupByPlatform(rates: Sub2Rate[]): [string, Sub2Rate[]][] {
  const grouped = new Map<string, Sub2Rate[]>();
  for (const rate of rates) {
    const key = rate.platform || "unknown";
    grouped.set(key, [...(grouped.get(key) ?? []), rate]);
  }
  return Array.from(grouped.entries()).sort(([left], [right]) =>
    platformLabel(left).localeCompare(platformLabel(right))
  );
}

function generateAccessKey(prefix = "apim"): string {
  const bytes = new Uint8Array(18);
  window.crypto.getRandomValues(bytes);
  const body = Array.from(bytes, (item) => item.toString(36).padStart(2, "0")).join("");
  return `${prefix}-${body.slice(0, 12)}-${body.slice(12, 24)}-${body.slice(24, 36)}`;
}

function App() {
  const [authStatus, setAuthStatus] = useState<AuthStatus | null>(null);
  const [configs, setConfigs] = useState<ApiConfig[]>([]);
  const [history, setHistory] = useState<CheckRecord[]>([]);
  const [admins, setAdmins] = useState<Admin[]>([]);
  const [messages, setMessages] = useState<ReceivedMessage[]>([]);
  const [sendFailures, setSendFailures] = useState<SendFailure[]>([]);
  const [statusBars, setStatusBars] = useState<ConfigStatusBars[]>([]);
  const [sub2Prices, setSub2Prices] = useState<Sub2PriceBoard[]>([]);
  const [oneBotSettings, setOneBotSettings] = useState<OneBotSettings | null>(null);
  const [commandSettings, setCommandSettings] = useState<CommandSetting[]>([]);
  const [expandedSub2, setExpandedSub2] = useState<Record<number, boolean>>({});
  const [historyExpanded, setHistoryExpanded] = useState(false);
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
    const [
      nextStatus,
      nextConfigs,
      nextAdmins,
      nextMessages,
      nextSendFailures,
      nextStatusBars,
      nextSub2Prices,
      nextOneBotSettings,
      nextCommandSettings
    ] = await Promise.all([
      requestJson<AppStatus>("/api/status"),
      requestJson<ApiConfig[]>("/api/configs"),
      requestJson<Admin[]>("/api/admins"),
      requestJson<ReceivedMessage[]>("/api/messages/recent"),
      requestJson<SendFailure[]>("/api/sends/recent-failures"),
      requestJson<ConfigStatusBars[]>("/api/status-bars"),
      requestJson<Sub2PriceBoard[]>("/api/sub2/prices"),
      requestJson<OneBotSettings>("/api/settings/onebot"),
      requestJson<CommandSetting[]>("/api/settings/commands")
    ]);
    setStatus(nextStatus);
    setConfigs(nextConfigs);
    setAdmins(nextAdmins);
    setMessages(nextMessages);
    setSendFailures(nextSendFailures);
    setStatusBars(nextStatusBars);
    setSub2Prices(nextSub2Prices);
    setOneBotSettings(nextOneBotSettings);
    setCommandSettings(nextCommandSettings);
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

  async function refreshAuthStatus() {
    const next = await requestJson<AuthStatus>("/api/webui/auth-status");
    setAuthStatus(next);
    return next;
  }

  useEffect(() => {
    refreshAuthStatus().catch((err) => setError(err.message));
  }, []);

  useEffect(() => {
    if (!authStatus?.authenticated) return;
    loadAll().catch((err) => {
      if (err instanceof Error && err.message.includes("401")) {
        clearAuthToken();
        setAuthStatus({ configured: true, authenticated: false });
      } else {
        setError(err instanceof Error ? err.message : "加载失败");
      }
    });
    const timer = window.setInterval(() => {
      loadAll().catch((err) => setError(err instanceof Error ? err.message : "加载失败"));
    }, 15000);
    return () => window.clearInterval(timer);
  }, [authStatus?.authenticated]);

  useEffect(() => {
    if (!authStatus?.authenticated) return;
    loadHistory(selectedConfig?.name).catch((err) => setError(err.message));
  }, [selectedConfig?.name, authStatus?.authenticated]);

  function showNotice(message: string) {
    setNotice(message);
    setError(null);
    window.setTimeout(() => setNotice(null), 3500);
  }

  async function completeAuth(token: string) {
    setAuthToken(token);
    const next = await refreshAuthStatus();
    if (next.authenticated) {
      await loadAll();
    }
  }

  async function setupWebUI(secret: string) {
    setBusy("webui-setup");
    try {
      const result = await requestJson<{ token: string }>("/api/webui/setup", {
        method: "POST",
        body: JSON.stringify({ secret })
      });
      await completeAuth(result.token);
      showNotice("WebUI 进入密钥已设置");
    } catch (err) {
      setError(err instanceof Error ? err.message : "设置密钥失败");
    } finally {
      setBusy(null);
    }
  }

  async function loginWebUI(secret: string) {
    setBusy("webui-login");
    try {
      const result = await requestJson<{ token: string }>("/api/webui/login", {
        method: "POST",
        body: JSON.stringify({ secret })
      });
      await completeAuth(result.token);
      showNotice("已登录");
    } catch (err) {
      setError(err instanceof Error ? err.message : "登录失败");
    } finally {
      setBusy(null);
    }
  }

  function logoutWebUI() {
    clearAuthToken();
    setAuthStatus({ configured: true, authenticated: false });
    setConfigs([]);
    setHistory([]);
    setNotice("已退出 WebUI");
  }

  async function saveOneBotSettings(data: { ws_url: string; access_token: string; ws_token_in_query: boolean }) {
    setBusy("onebot-settings");
    try {
      await requestJson<OneBotSettings>("/api/settings/onebot", {
        method: "PUT",
        body: JSON.stringify({
          ws_url: data.ws_url,
          access_token: data.access_token.trim() ? data.access_token : null,
          ws_token_in_query: data.ws_token_in_query
        })
      });
      await loadAll();
      showNotice("OneBot WebSocket 设置已保存");
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存 OneBot 设置失败");
    } finally {
      setBusy(null);
    }
  }

  async function toggleCommand(command: CommandSetting) {
    setBusy(`command-${command.command}`);
    try {
      await requestJson<CommandSetting>(`/api/settings/commands/${encodeURIComponent(command.command.replace(/^\//, ""))}`, {
        method: "PATCH",
        body: JSON.stringify({ enabled: !command.enabled })
      });
      await loadAll();
      showNotice(`${command.label} 已${command.enabled ? "关闭" : "开启"}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "更新命令开关失败");
    } finally {
      setBusy(null);
    }
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

  async function editTarget(config: ApiConfig) {
    const nextTarget = window.prompt("编辑通知对象，格式：G群号 或 PQQ号，多个用 & 连接", config.target)?.trim();
    if (nextTarget == null || nextTarget === config.target) return;
    if (!nextTarget) {
      setError("通知对象不能为空");
      return;
    }
    setBusy(`target-${config.name}`);
    try {
      await requestJson<ApiConfig>(`/api/configs/${encodeURIComponent(config.name)}`, {
        method: "PATCH",
        body: JSON.stringify({ target: nextTarget })
      });
      await loadAll();
      showNotice("通知对象已更新");
    } catch (err) {
      setError(err instanceof Error ? err.message : "更新通知对象失败");
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
  const quickStartComplete = Boolean(oneBotSettings?.connected && admins.length > 0 && configs.length > 0);

  if (!authStatus) {
    return (
      <AuthShell title="正在检查 WebUI 进入密钥" icon={<LockKeyhole />}>
        <div className="empty">正在加载鉴权状态。</div>
      </AuthShell>
    );
  }

  if (!authStatus.configured) {
    return (
      <AuthSetup
        busy={busy === "webui-setup"}
        error={error}
        onSubmit={setupWebUI}
      />
    );
  }

  if (!authStatus.authenticated) {
    return (
      <AuthLogin
        busy={busy === "webui-login"}
        error={error}
        onSubmit={loginWebUI}
      />
    );
  }

  return (
    <main className="app-shell">
      <header className="topbar">
        <div>
          <p className="eyebrow">OneBot API Monitor</p>
          <h1>APIMonitorBot</h1>
        </div>
        <div className="topbar-actions">
          <button className="icon-button" onClick={() => loadAll().catch((err) => setError(err.message))} aria-label="刷新">
            <RefreshCcw size={18} />
          </button>
          <button onClick={logoutWebUI}>
            <LogIn size={16} />
            退出
          </button>
        </div>
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

      {!quickStartComplete && (
        <QuickStartPanel
          oneBotSettings={oneBotSettings}
          admins={admins}
          configs={configs}
          busy={busy === "onebot-settings"}
          onSaveOneBot={saveOneBotSettings}
        />
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
                      <td>
                        <div className="editable-cell target-cell">
                          <span className="target-text">{config.target}</span>
                          <button
                            className="icon-button tiny edit-button"
                            onClick={() => editTarget(config)}
                            disabled={busy === `target-${config.name}`}
                            aria-label={`编辑通知对象 ${config.target}`}
                            title="编辑通知对象"
                          >
                            <Pencil size={14} />
                          </button>
                        </div>
                      </td>
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
            <Field label="报告目标" value={form.target} placeholder="G123456789 或 P2087900785，多个用 & 连接" onChange={(target) => setForm({ ...form, target })} required />
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

      <Sub2PriceSection
        boards={sub2Prices}
        expanded={expandedSub2}
        onToggle={(configId) =>
          setExpandedSub2((current) => ({ ...current, [configId]: !current[configId] }))
        }
        timeZone={displayTimeZone}
      />

      <CommandSettingsPanel
        commands={commandSettings}
        busy={busy}
        onToggle={toggleCommand}
      />

      <section className="panel send-failures-panel">
        <div className="panel-heading">
          <div>
            <h2>最近发送失败</h2>
            <p>最近 10 条 OneBot 发送失败记录，用于排查 WebSocket、token 和群权限问题。</p>
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
                  {item.status_code ? `状态码 ${item.status_code} · ` : ""}
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

      <section className={historyExpanded ? "panel history-panel open" : "panel history-panel collapsed"}>
        <div className="panel-heading">
          <div>
            <h2>巡检历史</h2>
            <p>{selectedConfig ? `${selectedConfig.name} 最近 ${HISTORY_LIMIT} 条记录` : "选择一个配置查看记录"}</p>
          </div>
          <button
            className="history-toggle"
            type="button"
            aria-expanded={historyExpanded}
            onClick={() => setHistoryExpanded((value) => !value)}
          >
            {historyExpanded ? <ChevronDown size={18} /> : <ChevronRight size={18} />}
            {historyExpanded ? "收起" : "展开"}
          </button>
        </div>
        {historyExpanded && (
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
        )}
      </section>

      <footer className="footer">
        <Bot size={16} />
        WebSocket {status?.onebot_ws_configured ? "已配置" : "未配置"}
        {` · 时区 ${displayTimeZone}`}
        {status?.onebot_ws_last_error ? ` · ${status.onebot_ws_last_error}` : ""}
      </footer>
    </main>
  );
}

function AuthShell({ title, icon, children }: { title: string; icon: React.ReactNode; children: React.ReactNode }) {
  return (
    <main className="auth-shell">
      <section className="panel auth-panel">
        <div className="panel-heading">
          <div>
            <p className="eyebrow">APIMonitorBot</p>
            <h1>{title}</h1>
          </div>
          {icon}
        </div>
        {children}
      </section>
    </main>
  );
}

function AuthSetup({
  busy,
  error,
  onSubmit
}: {
  busy: boolean;
  error: string | null;
  onSubmit: (secret: string) => void;
}) {
  const [secret, setSecret] = useState("");
  return (
    <AuthShell title="设置 WebUI 进入密钥" icon={<LockKeyhole size={22} />}>
      <form className="auth-form" onSubmit={(event) => { event.preventDefault(); onSubmit(secret); }}>
        <p className="auth-copy">首次进入需要设置一个本机 WebUI 密钥。后续打开后台时使用它登录。</p>
        {error && <div className="toast error">{error}</div>}
        <Field label="进入密钥" value={secret} onChange={setSecret} type="password" required />
        <div className="button-row">
          <button type="button" onClick={() => setSecret(generateAccessKey("webui"))}>
            <WandSparkles size={16} />
            自动生成
          </button>
          <button className="primary no-margin" type="submit" disabled={busy}>
            <KeyRound size={16} />
            保存并进入
          </button>
        </div>
      </form>
    </AuthShell>
  );
}

function AuthLogin({
  busy,
  error,
  onSubmit
}: {
  busy: boolean;
  error: string | null;
  onSubmit: (secret: string) => void;
}) {
  const [secret, setSecret] = useState("");
  return (
    <AuthShell title="登录 WebUI" icon={<LockKeyhole size={22} />}>
      <form className="auth-form" onSubmit={(event) => { event.preventDefault(); onSubmit(secret); }}>
        {error && <div className="toast error">{error}</div>}
        <Field label="进入密钥" value={secret} onChange={setSecret} type="password" required />
        <p className="auth-hint">
          密钥不会明文保存；忘记时到本机数据库 data/apimonitor.sqlite3 的 app_settings 表删除 key=webui.secret_hash 后重启重新设置。
        </p>
        <button className="primary no-margin" type="submit" disabled={busy}>
          <LogIn size={16} />
          进入后台
        </button>
      </form>
    </AuthShell>
  );
}

function QuickStartPanel({
  oneBotSettings,
  admins,
  configs,
  busy,
  onSaveOneBot
}: {
  oneBotSettings: OneBotSettings | null;
  admins: Admin[];
  configs: ApiConfig[];
  busy: boolean;
  onSaveOneBot: (data: { ws_url: string; access_token: string; ws_token_in_query: boolean }) => void;
}) {
  const [wsUrl, setWsUrl] = useState("ws://127.0.0.1:3001");
  const [accessToken, setAccessToken] = useState("");
  const [tokenInQuery, setTokenInQuery] = useState(true);

  useEffect(() => {
    if (!oneBotSettings) return;
    setWsUrl(oneBotSettings.ws_url || "ws://127.0.0.1:3001");
    setTokenInQuery(oneBotSettings.ws_token_in_query);
  }, [oneBotSettings?.ws_url, oneBotSettings?.ws_token_in_query]);

  const steps = [
    { label: "WebUI 密钥", done: true },
    { label: "NapCat WebSocket", done: Boolean(oneBotSettings?.ws_url) },
    { label: "管理员 QQ", done: admins.length > 0 },
    { label: "API 配置", done: configs.length > 0 }
  ];

  return (
    <section className="panel quickstart-panel">
      <div className="panel-heading">
        <div>
          <h2>快速上手</h2>
          <p>按顺序完成 WebUI、NapCat、管理员和 API 配置。OneBot 第一版推荐只使用 WebSocket。</p>
        </div>
        <ClipboardList size={18} />
      </div>
      <div className="quickstart-grid">
        <div className="quickstart-steps">
          {steps.map((step, index) => (
            <div className={step.done ? "quickstep done" : "quickstep"} key={step.label}>
              {step.done ? <CheckCircle2 size={17} /> : <Circle size={17} />}
              <span>{index + 1}. {step.label}</span>
            </div>
          ))}
        </div>
        <form className="onebot-setup" onSubmit={(event) => { event.preventDefault(); onSaveOneBot({ ws_url: wsUrl, access_token: accessToken, ws_token_in_query: tokenInQuery }); }}>
          <div className="section-label">
            <Cable size={16} />
            NapCat WebSocket 服务端
          </div>
          <p className="guide-text">
            在 NapCat 的网络配置里开启 WebSocket Server，监听地址建议 127.0.0.1，端口建议 3001。先在 NapCat 上设置 token，再复制到这里。
          </p>
          <div className="napcat-code">
            本项目填写：{wsUrl || "ws://127.0.0.1:3001"}
            {tokenInQuery && (accessToken || oneBotSettings?.access_token_configured) ? "?access_token=NapCat中配置的token" : ""}
          </div>
          <Field label="WebSocket 地址" value={wsUrl} placeholder="ws://127.0.0.1:3001" onChange={setWsUrl} />
          <label className="field">
            <span>NapCat token {oneBotSettings?.access_token_preview ? `当前 ${oneBotSettings.access_token_preview}` : ""}</span>
            <input
              value={accessToken}
              onChange={(event) => setAccessToken(event.target.value)}
              placeholder="复制 NapCat 上已配置的 token，留空则沿用现有值"
              autoComplete="off"
            />
          </label>
          <label className="switch-row">
            <input
              type="checkbox"
              checked={tokenInQuery}
              onChange={(event) => setTokenInQuery(event.target.checked)}
            />
            <span>连接时把 NapCat token 放到 query 参数里</span>
          </label>
          <div className="button-row">
            <button className="primary no-margin" disabled={busy} type="submit">
              <Settings2 size={16} />
              保存并重连
            </button>
          </div>
          <div className={oneBotSettings?.connected ? "connection-state ok" : "connection-state warn"}>
            {oneBotSettings?.connected ? "WebSocket 已连接" : oneBotSettings?.last_error || "WebSocket 未连接"}
          </div>
        </form>
      </div>
    </section>
  );
}

function CommandSettingsPanel({
  commands,
  busy,
  onToggle
}: {
  commands: CommandSetting[];
  busy: string | null;
  onToggle: (command: CommandSetting) => void;
}) {
  return (
    <section className="panel command-settings-panel">
      <div className="panel-heading">
        <div>
          <h2>命令开关</h2>
          <p>关闭后，群聊和私聊内对应命令都会返回“该命令已关闭”。/cancel 始终保留。</p>
        </div>
        <Settings2 size={18} />
      </div>
      <div className="command-grid">
        {commands.map((command) => (
          <div className="command-toggle-row" key={command.command}>
            <div>
              <strong>{command.command}</strong>
              <span>{command.label} · {command.description}</span>
            </div>
            <label className="toggle">
              <input
                type="checkbox"
                checked={command.enabled}
                disabled={busy === `command-${command.command}`}
                onChange={() => onToggle(command)}
              />
              <span>{command.enabled ? "开启" : "关闭"}</span>
            </label>
          </div>
        ))}
      </div>
    </section>
  );
}

function Sub2PriceSection({
  boards,
  expanded,
  onToggle,
  timeZone
}: {
  boards: Sub2PriceBoard[];
  expanded: Record<number, boolean>;
  onToggle: (configId: number) => void;
  timeZone: string;
}) {
  return (
    <section className="panel sub2-panel">
      <div className="panel-heading">
        <div>
          <h2>Sub2API 渠道倍率</h2>
          <p>默认收起，展开后查看各渠道当前倍率、涨跌百分比和历史变化。</p>
        </div>
        <BadgePercent size={18} />
      </div>
      <div className="sub2-board-list">
        {boards.length === 0 ? (
          <div className="empty">暂无 Sub2API 渠道倍率数据。</div>
        ) : (
          boards.map((board) => {
            const isOpen = Boolean(expanded[board.config_id]);
            const changedCount = board.rates.filter((rate) => trendClass(rate.change_percent) !== "flat").length;
            return (
              <div className="sub2-board" key={board.config_id}>
                <button
                  className="sub2-board-toggle"
                  type="button"
                  aria-expanded={isOpen}
                  onClick={() => onToggle(board.config_id)}
                >
                  {isOpen ? <ChevronDown size={18} /> : <ChevronRight size={18} />}
                  <span className="sub2-board-main">
                    <strong>{board.name}</strong>
                    <span>{board.target} · {board.base_url}</span>
                  </span>
                  <span className="sub2-board-summary">
                    <span>{board.rates.length} 个分组</span>
                    <span>{changedCount} 个变化</span>
                    <span>{formatTime(board.last_checked_at, timeZone)}</span>
                  </span>
                </button>
                {isOpen && (
                  <div className="sub2-board-body">
                    {board.last_error && <div className="sub2-error">最近错误：{board.last_error}</div>}
                    {groupByPlatform(board.rates).map(([platform, rates]) => (
                      <div className="sub2-platform" key={`${board.config_id}-${platform}`}>
                        <div className={`sub2-platform-badge ${platformClass(platform)}`}>
                          {platformLabel(platform)}
                        </div>
                        <div className="sub2-rate-grid">
                          {rates
                            .slice()
                            .sort((left, right) => left.group_name.localeCompare(right.group_name))
                            .map((rate) => (
                              <Sub2RateCard key={`${rate.platform}-${rate.group_key}`} rate={rate} timeZone={timeZone} />
                            ))}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            );
          })
        )}
      </div>
    </section>
  );
}

function Sub2RateCard({ rate, timeZone }: { rate: Sub2Rate; timeZone: string }) {
  const trend = trendClass(rate.change_percent);
  const TrendIcon = trend === "up" ? TrendingUp : trend === "down" ? TrendingDown : BadgePercent;
  return (
    <div className={`sub2-rate-card ${trend}`}>
      <div className="sub2-rate-head">
        <strong>{rate.group_name}</strong>
        <span>{formatTime(rate.last_seen_at, timeZone)}</span>
      </div>
      <div className="sub2-rate-body">
        <div>
          <div className={`sub2-current-rate ${trend}`}>{formatRate(rate.rate_multiplier)}</div>
          <div className={`sub2-rate-change ${trend}`}>
            <TrendIcon size={16} />
            <span>{formatPercent(rate.change_percent)}</span>
            {rate.previous_rate != null && <small>上次 {formatRate(rate.previous_rate)}</small>}
          </div>
        </div>
        <Sub2Sparkline points={rate.history} trend={trend} timeZone={timeZone} />
      </div>
    </div>
  );
}

function Sub2Sparkline({
  points,
  trend,
  timeZone
}: {
  points: Sub2RateHistoryPoint[];
  trend: "up" | "down" | "flat";
  timeZone: string;
}) {
  const chartPoints = downsamplePoints(points, 48);
  const width = 210;
  const height = 60;
  const values = chartPoints.map((point) => point.rate_multiplier);
  const minimum = values.length ? Math.min(...values) : 0;
  const maximum = values.length ? Math.max(...values) : 0;
  const color = trend === "up" ? "#ef4444" : trend === "down" ? "#22c55e" : "#94a3b8";
  const polyline = chartPoints
    .map((point, index) => {
      const x = chartPoints.length <= 1 ? 0 : (index / (chartPoints.length - 1)) * width;
      const ratio = maximum === minimum ? 0.5 : (point.rate_multiplier - minimum) / (maximum - minimum);
      const y = height - ratio * height;
      return `${x.toFixed(2)},${y.toFixed(2)}`;
    })
    .join(" ");
  const first = chartPoints[0];
  const last = chartPoints[chartPoints.length - 1];

  return (
    <div className="sub2-sparkline" aria-label="倍率历史折线图">
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-hidden="true">
        <line x1="0" y1={height} x2={width} y2={height} />
        {polyline && <polyline points={polyline} style={{ stroke: color }} />}
      </svg>
      <div className="sub2-sparkline-axis">
        <span>{first ? formatShortDate(first.recorded_at, timeZone) : "-"}</span>
        <span>{last ? formatShortDate(last.recorded_at, timeZone) : "-"}</span>
      </div>
    </div>
  );
}

function downsamplePoints<T>(points: T[], limit: number): T[] {
  if (points.length <= limit) return points;
  const step = (points.length - 1) / (limit - 1);
  return Array.from({ length: limit }, (_, index) => points[Math.round(index * step)]);
}

function formatShortDate(value: string, timeZone = defaultTimeZone): string {
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone,
    month: "2-digit",
    day: "2-digit"
  }).format(parseApiDate(value));
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
