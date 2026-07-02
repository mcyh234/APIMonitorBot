# Agent 开发指南

这份文档面向后续接手 APIMonitorBot 的 Agent 或开发者。目标是把本项目已经确定的产品要求、交互风格、工程约束、测试方式和已调用的 skill 固化下来，避免后续改动偏离当前方向。

## 协作风格

- 默认使用中文沟通和中文用户文案。
- 用户给出明确需求时，优先直接实现、验证并汇报结果，不停留在方案描述。
- 修改文件前先简短说明将要改什么；完成后说明改动点、验证结果和可访问路径。
- 遇到用户已有改动时不要回滚，先读懂再在其基础上继续。
- 项目所有源码、文档、配置样例按 UTF-8 维护。
- 面向 GitHub 发布时，不要提交 `.env`、数据库、虚拟环境、构建产物、缓存、压缩包和本地日志。

## 已调用和应优先使用的 Skills

- `ui-ux-pro-max`：已用于 WebUI 相关开发。后续只要涉及页面结构、状态图、按钮、表单、可访问性、响应式或视觉风格，继续先读并遵守该 skill。
- `openai-docs`：当前实现未调用。后续如果需要确认 OpenAI 官方 API、模型、Responses API 或 OpenAI 产品行为，应调用该 skill，并优先使用官方文档。
- `browser:control-in-app-browser`：当前实现未调用。后续如果要人工验证本地 WebUI 截图、点击流程或响应式页面，可使用该 skill。

## 产品定位

APIMonitorBot 是本机运行的 API 可用性监控服务：

- 后端：FastAPI + SQLite + SQLAlchemy + APScheduler。
- 前端：React + Vite + TypeScript。
- 通知：OneBot v11 HTTP 发消息，WebSocket 或 HTTP webhook 收消息。
- 第一版默认本机使用，WebUI 监听 `127.0.0.1`，暂不做登录鉴权。
- 通知发送层要保留未来适配 Telegram 的空间。

## OneBot 要求

- 发消息使用 OneBot v11 HTTP API：
  - `send_group_msg`
  - `send_private_msg`
- HTTP 发送必须支持 `Authorization: Bearer <ONEBOT_ACCESS_TOKEN>`。
- 收消息优先使用 WebSocket，连接时携带 Bearer token。
- WebSocket 需要兼容 query token：`ONEBOT_WS_TOKEN_IN_QUERY=true`。
- 保留 HTTP webhook 入口：`POST /onebot/webhook`。
- 图片发送使用 CQ 码 `base64://`，发送记录只保存短预览，例如 `[image:status.png]`，不要把完整 base64 写入 SQLite。

## 管理员和权限

- 默认管理员 QQ：`2087900785`。
- WebUI 支持维护多个管理员。
- 管理员不受命令冷却限制，可以随便调用命令。
- 普通用户调用 `/check`、`/status`、`/stat` 有 5 分钟冷却。
- 群聊普通用户只能操作或查看绑定本群通知对象的配置。
- 私聊命令默认只允许管理员，除非后续明确开放。

## 命令系统

必须保留并维护这些命令：

- `/addapi`：管理员专用，多轮对话添加 API。
- `/list`：管理员专用，列出所有配置。
- `/remove <apiname>`：管理员专用，删除配置。
- `/check <apiname>`：立即检查一次配置。
- `/status [apiname]`：生成 API 状态条 PNG 并发送。
- `/stat`：抓取 `https://status.gptstore.club/` 全页快照，切到 1 小时视图后发送。
- `/cancel`：取消多轮对话。

`/addapi` 流程必须保持：

1. 请输入 api 配置名称。
2. 请输入报告群号/私聊 QQ 号，格式为 `G群号` 或 `PQQ号`。
3. 如果是群聊目标，提示机器人在或不在该群。
4. 请输入 BaseURL。
5. 请输入 APIKey，且 APIKey 必须在私聊中完成收集。
6. 请输入监听模型名称。
7. 保存前调用 OpenAI 兼容 `chat/completions` 发送 `hi` 做验证。
8. 验证成功后入库并启用巡检。

## API 探测规则

- 探测请求：

```text
POST {BaseURL}/chat/completions
```

- 请求体使用 `hi` 用户消息。
- 判定可用必须满足 HTTP 2xx、响应 JSON 可解析、存在 assistant 内容。
- `verify_ssl` 必须保持 `false`，用于兼容上游证书链不完整。
- 401、403、429、5xx、404、JSON 异常、空 assistant 内容都视为不可用。
- `TIMEOUT` 不计入 API 可用性，不写巡检记录，不向业务群通知。
- `NETWORK_ERROR` 不计入 API 可用性，不写巡检记录，不向业务群通知，静默忽略。
- `TIMEOUT` 出现时检查 Google 连通性；如果 Google 也不可用，私聊通知默认管理员“当前国际互联网连接断开”。

## 定时巡检和通知

- 默认每 1 分钟巡检所有启用配置。
- 夜间省流默认启用：00:00 到 08:00 改为每 10 分钟实际探测一次。
- 首次失败后立即二次确认；二次仍失败才进入不可用状态。
- 首次确认不可用，立即发送“当前出现业务中断”。
- 10 分钟内不重复发送不可用通知。
- 10 次巡检后仍未恢复，发送“10分钟仍未恢复业务”，之后保持静默。
- 故障后连续 2 次恢复成功，发送“当前服务恢复可用”。
- 通知文案中的可用性表述使用“最近请求成功率”，不要再写“今日可用性占比”。
- 同一通知群或通知对象同一轮出现多个 API 状态变化时，合并成一条报告。

## 状态条图

WebUI 和 `/status` 都要使用一致的状态聚合逻辑。

- WebUI 展示最近 30 分钟、5 小时、24 小时。
- `/status` 无参数时：
  - 群聊：发送本群通知对象绑定的所有接口状态，合成一张图。
  - 私聊：管理员可查看全部接口。
- 颜色语义：
  - 灰色：未检查。
  - 绿色：200 可用或该桶全部成功。
  - 黄色：部分时间可用。
  - 红色：503、404 等不可用状态或该桶全部失败。
- 状态条只统计定时巡检记录，手动检查不改变状态图。

## `/stat` 网页快照

- 访问 `https://status.gptstore.club/`。
- 截图整个页面从上到下。
- 请求成功率时间必须切到 `1 小时`。
- 冷却 5 分钟仅限制普通用户，管理员不限制。
- 发送到当前通知对象：
  - 群聊：当前群是绑定通知群，或发送者是管理员。
  - 私聊：仅管理员。
- 当前实现使用本机 Edge/Chrome 的 DevTools 协议，不依赖 Playwright 下载浏览器。

## WebUI 要求

WebUI 是运维后台，不是营销页。

- 设计应紧凑、清晰、适合重复使用和快速扫描。
- 保持现有深色 dashboard 风格，不引入大面积装饰背景。
- 使用 lucide-react 图标，不用 emoji 作为结构性图标。
- 控件要有稳定尺寸，避免刷新或 hover 造成布局跳动。
- 需要展示：
  - API 配置和状态。
  - 最近请求成功率。
  - 最近检查时间，按 `Asia/Shanghai` 正确显示。
  - 最近 10 条接收到的消息，触发机器人动作的记录高亮。
  - 最近发送失败原因。
  - API 名称、模型名称编辑按钮。
  - 状态条图。
  - 管理员管理。

## 数据和安全

- APIKey 和 OneBot token 不应在 Web API 中返回明文。
- `SECRET_MASTER_KEY` 只存在 `.env`，不可提交。
- `.env`、`data/`、`.venv/`、`frontend/node_modules/`、`frontend/dist/`、`release/` 必须忽略。
- 打包 GitHub zip 前必须检查压缩包不包含 `.env`、数据库、缓存和构建产物。

## 工程约束

- 后端新增行为优先补单元测试或集成测试。
- 前端新增功能至少保证 `npm run build` 通过。
- 后端改动后运行：

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m compileall -q backend run.py scripts tests
```

- 前端改动后运行：

```powershell
cd frontend
npm run build
```

- 手动代码编辑使用 patch，不用临时脚本覆写源码。
- Windows PowerShell 下不要使用 Bash heredoc；需要 inline Python 时使用 PowerShell here-string 或 `python -c`。

## 发布打包要求

提交 GitHub 前建议生成源码 zip，但不要把 zip 本身提交。

包内应包含：

- `README.md`
- `LICENSE`
- `Prompt.txt`
- `.env.example`
- `backend/`
- `frontend/`
- `scripts/`
- `tests/`
- `requirements.txt`
- `requirements-dev.txt`
- `pyproject.toml`
- `run.py`

包内不应包含：

- `.env`
- `data/`
- `.git/`
- `.venv/`
- `release/`
- `frontend/node_modules/`
- `frontend/dist/`
- `__pycache__/`
- `.pytest_cache/`
- `*.pyc`
- `*.log`

## 待实现方向

- Telegram Bot 通知。
- WebUI 登录鉴权。
- Docker Compose 部署。
- 配置级巡检间隔和阈值。
- WebUI 编辑 BaseURL、APIKey、通知目标。
- 巡检历史导出。
- Prometheus 指标。
- 数据库迁移和版本升级流程。
