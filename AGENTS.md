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
- 通知：OneBot v11 WebSocket 收发消息；OneBot HTTP 发送配置已弃用。
- 第一版默认本机使用，WebUI 监听 `127.0.0.1`，首次进入必须设置 WebUI 进入密钥。
- 通知发送层要保留未来适配 Telegram 的空间。

## OneBot 要求

- 推荐 OneBot v11 WebSocket Server 单连接模式，NapCat 推荐开启 WebSocket Server。
- WebSocket 连接既用于接收事件，也优先用于发送 `send_group_msg`、`send_private_msg`、`get_group_list` 等 action。
- 收消息使用 WebSocket，连接时携带 Bearer token。
- WebSocket 需要兼容 query token：`ONEBOT_WS_TOKEN_IN_QUERY=true`。
- 不要在 WebUI、README 或 `.env.example` 中引导配置 OneBot HTTP 发送；新部署只配置 WebSocket Server 和 NapCat token，避免重复发送。
- `/onebot/webhook` 仅保留兼容响应，不再处理消息事件；避免 HTTP webhook 与 WebSocket 同时触发命令。
- 图片发送使用 CQ 码 `base64://`，发送记录只保存短预览，例如 `[image:status.png]`，不要把完整 base64 写入 SQLite。

## 管理员和权限

- 默认管理员 QQ：`2087900785`。
- WebUI 支持维护多个管理员。
- 管理员不受命令冷却限制，可以随便调用命令。
- 普通用户调用 `/check`、`/status`、`/stat`、`/price`、`/radar`、`/tibo` 有 5 分钟冷却。
- 群聊普通用户只能操作或查看绑定本群通知对象的配置；配置支持多个通知对象，例如 `G123456789&P1122334455`。
- 私聊命令默认只允许管理员，除非后续明确开放。

## 命令系统

必须保留并维护这些命令：

- `/addapi`：管理员专用，多轮对话添加 API。
- `/list`：管理员专用，列出所有配置。
- `/remove <apiname>`：管理员专用，删除配置。
- `/check [apiname]`：立即检查配置；不带参数时检查当前通知对象绑定的所有 API，命中多条时用图片渲染结果，避免发送过长文本。
- `/status [apiname]`：生成 API 状态条 PNG 并发送。
- `/stat`：抓取 `https://status.gptstore.club/` 全页快照，切到 1 小时视图后发送。
- `/addsub2`：管理员专用，多轮对话添加 Sub2API 渠道倍率监控。
- `/price`：发送当前通知对象绑定的 Sub2API 价格表图片。
- `/up`、`up`：对全 Bot 整体 Token 倍率投看涨票；`/down`、`down`：投看跌票。每个 QQ 每个上海自然日一票，可在当天改票。
- `/radar`：读取 Codex Radar 公开摘要并发送模型 IQ 降智趋势图。
- `/tibo`：读取 Tibo 最新公开 X 帖子和 `tibo_presence`，发送带中文翻译的白底雷达图。
- `/cancel`：取消多轮对话。

除 `/cancel` 外，上述命令都必须支持 WebUI 命令开关；关闭后命令返回“该命令已关闭。”。

`/addapi` 流程必须保持：

1. 请输入 api 配置名称。
2. 请输入报告群号/私聊 QQ 号，格式为 `G群号` 或 `PQQ号`，多个通知对象用 `&` 连接，例如 `G123456789&P1122334455`。
3. 如果包含群聊目标，逐个提示机器人在或不在对应群。
4. 请输入 BaseURL。
5. 请输入 APIKey，且 APIKey 必须在私聊中完成收集。
6. 请输入监听模型名称。
7. 保存前调用 OpenAI 兼容 `chat/completions` 发送 `hi` 做验证。
8. 验证成功后入库并启用巡检。

`/addsub2` 流程必须保持：

1. 请输入 API 名称。
2. 请输入 Sub2API 的 BaseURL。
3. 请输入 email。
4. 请输入密码。
5. 登录并读取渠道成功后，提示输入报告群号/私聊 QQ 号。
6. 目标格式为 `G群号` 或 `PQQ号`，多个通知对象用 `&` 连接。
7. 如果包含群聊目标，逐个提示机器人在或不在对应群。
8. 添加成功后立即保存当前渠道倍率，首次保存不发送价格变动通知。

## API 探测规则

- 探测请求：

```text
POST {BaseURL}/chat/completions
```

- 请求体使用 `hi` 用户消息。
- 探测请求体不要携带 `max_tokens`、`max_output_tokens` 等输出长度限制参数，避免 OpenAI 兼容网关返回 `Unsupported parameter`。
- 判定可用必须满足 HTTP 2xx、响应 JSON 可解析、存在 assistant 内容。
- `verify_ssl` 必须保持 `false`，用于兼容上游证书链不完整。
- 401、403、429、5xx、404、JSON 异常、空 assistant 内容都视为不可用。
- `TIMEOUT` 不计入 API 可用性，不写巡检记录，不向业务群通知。
- `NETWORK_ERROR` 不计入 API 可用性，不写巡检记录，不向业务群通知，静默忽略。
- `TIMEOUT` 出现时检查 Google 连通性；如果 Google 也不可用，私聊通知默认管理员“当前国际互联网连接断开”。

## 定时巡检和通知

- 默认每 1 分钟巡检所有启用配置。
- 夜间省流默认启用：00:00 到 08:00 改为每 10 分钟实际探测一次。
- WebUI 的“巡检与冷却”面板允许按分钟修改夜间省流时间范围、夜间巡检间隔和普通用户命令冷却时间；配置保存到 SQLite 并覆盖 `.env` 默认值，保存后立即生效。命令冷却设为 0 表示关闭普通用户冷却。
- 首次失败后立即二次确认；二次仍失败才进入不可用状态。
- 如果二次确认结果是 `400`、`404`、`429` 或 `EMPTY_ASSISTANT_CONTENT`，先尝试模型自动回退：优先把当前模型名中的 `5.5` 替换为 `5.4`，再按 `API_PROBE_FALLBACK_MODELS` 候选列表探测；如果回退模型探测成功，立即把该配置的模型名保存为成功模型，本轮记录为可用且不发送通报。
- 首次确认不可用，立即发送“当前出现业务中断”。
- 10 分钟内不重复发送不可用通知。
- 10 次巡检后仍未恢复，发送“10分钟仍未恢复业务”，之后保持静默。
- 故障后连续 2 次恢复成功，发送“当前服务恢复可用”。
- 通知文案中的可用性表述使用“最近请求成功率”，不要再写“今日可用性占比”。
- 同一通知群或通知对象同一轮出现多个 API 状态变化时，合并成一条报告；同一个 API 绑定多个通知对象时会分别投递到每个对象。

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

## Sub2API 渠道倍率监控

- 登录接口：`POST {BaseURL}/api/v1/auth/login`，请求体为 `{"email":"...","password":"..."}`。
- 渠道接口：`GET {BaseURL}/api/v1/groups/available`，使用 `Authorization: Bearer <access_token>`。
- `/price` 定价接口：`GET {BaseURL}/api/v1/channels/available`，从嵌套的 `groups[]` 读取 `rate_multiplier`，从 `supported_models[].pricing` 读取每 Token 的 `input_price`、`output_price`、`cache_write_price`、`cache_read_price`。
- Sub2API 按 `1 CNY = 1 USD` 计价单位换算，不使用外汇汇率；每 MTok 人民币价公式为 `每 Token 单价 × 1,000,000 × rate_multiplier`。
- 需要记录返回 `data[]` 中每个分组的 `platform`、`name` 和 `rate_multiplier`。
- 首次看到分组、每天首次成功巡检和后续每次倍率变化都要追加倍率历史记录，用于 WebUI 和 `/price` 折线图及最近 30 天日 K。
- token 和密码都要加密存储；token 未过期时复用，失效后重新登录。
- 每分钟检测一次启用的 Sub2API 配置，不受 API 夜间省流跳过影响。
- 首次添加只保存当前倍率，不通知。
- 后续倍率变化或分组被删除时发送图片通报，并在图片之后追加文字通报说明具体变化分组。
- 图片要求：
  - Anthropic 渠道用橙色。
  - OpenAI 渠道用绿色。
  - 发生价格变动的分组高亮。
  - 顶部列出 `旧倍率 -> 新倍率`；分组删除时显示“已删除”和最后倍率。
  - 每个分组展示当前倍率大字、涨跌百分比和按日期为 x 轴的历史折线。
  - 同时展示按 `Asia/Shanghai` 自然日聚合的倍率开、高、低、收 K 线；缺失日期留空，不推测数据。
  - 图片顶部显示全 Bot 当日看涨/看跌比例，红色看涨、绿色看跌、零票灰色。
  - `/price` 的模型价格只能来自 `/api/v1/channels/available`，不得使用内置静态模型价目表。
  - 上涨使用红色，下跌使用绿色。
  - `/price` 图片必须完整展开，不使用 WebUI 的收起状态。
- `/price` 检索当前通知对象存在的价格表：
  - 群聊：当前群绑定的 Sub2API 配置，支持匹配多通知对象中的任一群。
  - 私聊：管理员私聊中与该 QQ 私聊通知对象绑定的 Sub2API 配置，支持匹配多通知对象中的私聊目标。
  - 普通用户 5 分钟冷却，管理员不冷却。

## WebUI 要求

WebUI 是运维后台，不是营销页。

- 设计应紧凑、清晰、适合重复使用和快速扫描。
- 保持现有深色 dashboard 风格，不引入大面积装饰背景。
- 使用 lucide-react 图标，不用 emoji 作为结构性图标。
- 控件要有稳定尺寸，避免刷新或 hover 造成布局跳动。
- 需要展示：
  - 首次设置 WebUI 进入密钥；未设置时只显示设置密钥页面。
  - 忘记 WebUI 进入密钥时使用 `scripts/reset_webui_secret.py` 重置，不要手工指导用户改数据库。
  - 登录页；未登录时不能访问业务 API。
  - 快速上手面板，提供 NapCat WebSocket Server 配置教程和 OneBot WS 可视化设置；当 WS 已连接、已有管理员、已有至少一个 API 配置后自动隐藏。
  - API 配置和状态。
  - 最近请求成功率。
  - 最近检查时间，按 `Asia/Shanghai` 正确显示。
  - 最近 10 条接收到的消息，触发机器人动作的记录高亮。
  - 最近发送失败原因。
  - API 名称、模型名称编辑按钮。
  - 状态条图。
  - Sub2API 渠道倍率面板，默认收起，点击展开；展开后展示当前倍率、涨跌百分比、历史折线、30 日日 K 和全局投票比例。
  - 命令开关，位于 Sub2API 渠道倍率面板下方，允许开启或关闭 `/addapi`、`/addsub2`、`/list`、`/remove`、`/check`、`/status`、`/stat`、`/price`、`/up`、`/down`、`/radar`、`/tibo`。
  - 管理员管理。
  - 版本升级面板，支持拖拽上传受信任的升级包、安装并自动重启，以及生成升级包。
  - 默认收起的“巡检与冷却”面板，支持修改夜间省流范围、夜间巡检间隔和普通用户命令冷却时间。
  - 右上角提供 `https://github.com/mcyh234/APIMonitorBot` 的 GitHub 图标入口；添加 API、管理员和版本升级面板默认收起，并持久化用户后续的折叠状态。

## 数据和安全

- APIKey 和 OneBot token 不应在 Web API 中返回明文。
- `SECRET_MASTER_KEY` 只存在 `.env`，不可提交。
- `.env`、`data/`、`.venv/`、`frontend/node_modules/`、`frontend/dist/`、`release/` 必须忽略。
- 打包 GitHub zip 前必须检查压缩包不包含 `.env`、数据库、缓存和构建产物。
- 升级包必须包含版本清单和 SHA-256，安装时限制到项目白名单路径，并在 `data/upgrades/backups/` 备份被覆盖文件；不得覆盖 `.env` 和 `data/`。

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
