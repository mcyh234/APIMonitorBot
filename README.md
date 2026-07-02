# APIMonitorBot

APIMonitorBot 是一个本机运行的 OpenAI 兼容 API 可用性监视器。它会定时探测多个 `BaseURL + APIKey + 模型` 配置，在服务中断、持续未恢复、恢复可用时，通过 OneBot v11 向 QQ 群聊或私聊推送通知。

项目包含 FastAPI 后端、React WebUI、SQLite 数据库、OneBot HTTP/WS 接入、管理员命令系统、多轮添加配置流程、APIKey 加密存储、状态条图和网页快照推送。

所有项目文件均按 UTF-8 维护。

## 功能概览

- 监控 OpenAI 兼容 `POST {BaseURL}/chat/completions` 接口。
- 支持多个 API 配置，每个配置可绑定一个 QQ 群或私聊通知对象。
- OneBot v11 HTTP 发消息，支持 `Authorization: Bearer <access_token>`。
- OneBot WebSocket 收消息，支持 access token header，也可兼容 query token。
- HTTP webhook 收消息备用入口：`POST /onebot/webhook`。
- SQLite 持久化，APIKey 使用 `SECRET_MASTER_KEY` 加密后入库。
- 默认管理员 QQ：`2087900785`，WebUI 可维护多个管理员。
- 管理员命令：`/addapi`、`/list`、`/remove`、`/check`、`/status`、`/stat`。
- `/addapi` 支持多轮对话添加 API，APIKey 强制在私聊内收集。
- 定时巡检失败会二次确认，减少偶发波动误报。
- 同一通知对象同一轮多 API 状态变化会合并报告，防止刷屏。
- `TIMEOUT` 和 `NETWORK_ERROR` 不计入业务中断通知。
- Timeout 时会额外检查 Google 连通性，如果国际网络也断开，会私聊通知默认管理员。
- WebUI 支持配置 CRUD、启停、手动检查、巡检历史、最近接收消息、发送失败原因、管理员管理。
- WebUI 状态条图展示最近 30 分钟、5 小时、24 小时接口状态。
- `/status` 生成 API 状态条 PNG 并发送到群聊/私聊。
- `/stat` 抓取 `https://status.gptstore.club/` 全页快照，切到 1 小时视图后发送图片。

## 技术栈

- 后端：FastAPI、Uvicorn、SQLAlchemy、SQLite、APScheduler、httpx、websockets、cryptography、Pillow。
- 前端：React、Vite、TypeScript、lucide-react。
- 运行环境：Python 3.11+，Node.js 20+ 推荐。

## 快速上手

### 1. 克隆或解压项目

```powershell
cd C:\Users\HelloWorld\Documents
git clone <your-repo-url> APIMonitorBot
cd APIMonitorBot
```

如果你使用的是本项目提供的 zip，直接解压后进入目录即可。

### 2. 创建 Python 虚拟环境

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

如果本机 pip 证书链异常，可临时使用：

```powershell
.\.venv\Scripts\python.exe -m pip install --trusted-host pypi.org --trusted-host files.pythonhosted.org -r requirements-dev.txt
```

### 3. 生成本地配置

```powershell
Copy-Item .env.example .env
.\.venv\Scripts\python.exe scripts\generate_secret_key.py
```

把生成的密钥填入 `.env`：

```env
SECRET_MASTER_KEY=粘贴生成的密钥
```

再按你的 OneBot 后端填写：

```env
ONEBOT_API_BASE_URL=http://127.0.0.1:5700
ONEBOT_WS_URL=ws://127.0.0.1:6700
ONEBOT_ACCESS_TOKEN=your-onebot-token
```

如果你的 OneBot WebSocket 需要把 token 放在 query 中：

```env
ONEBOT_WS_TOKEN_IN_QUERY=true
```

### 4. 构建 WebUI

```powershell
cd frontend
npm install
npm run build
cd ..
```

### 5. 启动服务

```powershell
.\.venv\Scripts\python.exe run.py
```

浏览器打开：

```text
http://127.0.0.1:8000
```

第一版 WebUI 默认只监听本机 `127.0.0.1`，暂不做登录鉴权。开放到局域网或公网前，请先补认证和访问控制。

## 常用配置

`.env.example` 已包含完整示例。常用项如下：

```env
APP_HOST=127.0.0.1
APP_PORT=8000
APP_TIMEZONE=Asia/Shanghai
DATABASE_URL=sqlite:///./data/apimonitor.sqlite3

ONEBOT_API_BASE_URL=http://127.0.0.1:5700
ONEBOT_WS_URL=ws://127.0.0.1:6700
ONEBOT_ACCESS_TOKEN=
ONEBOT_WS_TOKEN_IN_QUERY=false
ONEBOT_INBOUND_ACCESS_TOKEN=

DEFAULT_ADMIN_QQ=2087900785
CHECK_INTERVAL_SECONDS=60
NIGHT_SAVER_ENABLED=true
NIGHT_SAVER_START_HOUR=0
NIGHT_SAVER_END_HOUR=8
NIGHT_SAVER_INTERVAL_SECONDS=600

INTERNET_CHECK_URL=https://www.google.com/generate_204
INTERNET_CHECK_TIMEOUT_SECONDS=8
INTERNET_DISCONNECT_NOTIFY_COOLDOWN_SECONDS=600

STATUS_SNAPSHOT_URL=https://status.gptstore.club/
STATUS_SNAPSHOT_BROWSER_PATH=
STATUS_SNAPSHOT_TIMEOUT_SECONDS=45
STATUS_SNAPSHOT_VIEWPORT_WIDTH=1920
```

上游 API 探测默认 `verify_ssl=false`，用于兼容部分上游证书链不完整的 OpenAI 兼容网关。

## OneBot 接入说明

发消息使用 OneBot v11 HTTP API：

- `send_group_msg`
- `send_private_msg`

HTTP 请求会携带：

```http
Authorization: Bearer <ONEBOT_ACCESS_TOKEN>
```

收消息优先使用 WebSocket 连接 `ONEBOT_WS_URL`。备用 HTTP webhook：

```text
POST /onebot/webhook
Authorization: Bearer <ONEBOT_INBOUND_ACCESS_TOKEN>
```

仅当你的 OneBot HTTP POST 上报端能带 Bearer token 时，才需要配置 `ONEBOT_INBOUND_ACCESS_TOKEN`。

## 机器人命令

- `/addapi`：管理员专用，开始多轮对话添加 API。
- `/list`：管理员专用，列出所有配置。
- `/remove <apiname>`：管理员专用，删除指定配置。
- `/check <apiname>`：立即检查一次指定配置。群聊只能查绑定本群的配置，私聊仅管理员可用。
- `/status [apiname]`：发送 API 状态条 PNG。群聊无参数时显示本群绑定的所有配置，私聊管理员可看全部。
- `/stat`：抓取 GPTStore 状态页全页快照，切到 1 小时请求成功率视图后发送图片。
- `/cancel`：取消当前多轮对话。

普通用户调用 `/check`、`/status`、`/stat` 有 5 分钟冷却；管理员不受冷却限制。

## 监控与通知规则

探测请求：

```text
POST {BaseURL}/chat/completions
```

请求体会发送一条 `hi` 用户消息。判定为可用必须同时满足：

- HTTP 状态码为 2xx。
- 返回 JSON 能解析。
- `choices[0].message.content` 中存在 assistant 内容。

不可用通知策略：

- 首次失败后立即二次确认。
- 二次仍失败才进入不可用状态并发送“当前出现业务中断”。
- 10 分钟内不重复发送同类不可用通知。
- 10 次巡检后仍未恢复，发送“10分钟仍未恢复业务”。
- 此后保持静默。
- 故障后连续 2 次恢复成功，发送“当前服务恢复可用”。

特殊网络错误：

- `TIMEOUT`：不写入 API 巡检记录，不触发业务群中断通知；会检查 Google 连通性。
- `NETWORK_ERROR`：不写入 API 巡检记录，不触发业务群中断通知，静默忽略。

状态条颜色：

- 灰色：未检查。
- 绿色：该时间桶全部可用。
- 黄色：该时间桶部分可用。
- 红色：该时间桶全部不可用。

## WebUI

WebUI 首屏就是监控后台，不做营销页。主要区域：

- 系统概览指标。
- API 配置表。
- 新增 API 表单。
- 管理员 QQ 列表。
- 最近发送失败原因。
- 最近接收到的 OneBot 消息。
- 状态条图。
- 单配置巡检历史。

## 开发与测试

后续 Agent 或开发者接手前，请先阅读 [AGENTS.md](AGENTS.md)，其中记录了本项目的产品约束、交互风格、命令规则、UI 要求和打包注意事项。

后端测试：

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m compileall -q backend run.py scripts tests
```

前端构建：

```powershell
cd frontend
npm run build
```

前端开发模式：

```powershell
cd frontend
npm run dev
```

## 打包发布

提交 GitHub 前不要提交这些本地文件：

- `.env`
- `data/`
- `.venv/`
- `frontend/node_modules/`
- `frontend/dist/`
- `release/`
- `__pycache__/`
- `.pytest_cache/`

本仓库的 `.gitignore` 已默认排除这些路径。

项目根目录的 `Prompt.txt` 是本项目需求提示词归档，建议随源码一起提交，方便后续 Agent 或开发者追溯原始需求。

## 待实现功能

- Telegram Bot 通知适配。
- WebUI 登录鉴权与多用户角色。
- WebUI 支持编辑 BaseURL、APIKey、通知目标。
- WebUI 导出巡检历史 CSV。
- 支持更多探测方式，例如 `/models`、Responses API 或自定义请求体。
- 支持按配置自定义巡检间隔、超时时间、恢复阈值。
- 支持通知模板自定义。
- 支持 Docker Compose 一键部署。
- 支持数据库迁移脚本和版本升级流程。
- 支持 Prometheus 指标暴露。
- 支持更多 OneBot 适配器的图片发送兼容模式。

## 许可证

本项目使用 MIT License，详见 [LICENSE](LICENSE)。
