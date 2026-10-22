# Agent 开发指南

## 2026-10-22 安全修复

- SPA 静态文件兜底必须限制在前端构建目录内；路径遍历、盘符、UNC、设备名、ADS、点文件、符号链接及 Windows 重解析点须在访问不可信路径前拒绝，不得以 `Path.resolve()` 后才检查的方式处理 UNC。
- WebUI 鉴权使用 ASGI `scope["path"]`，不得使用受 Host 重建影响的 `request.url.path`。畸形或超长 Bearer token 应返回未授权，不可抛出 500。
- 缺失 `SECRET_MASTER_KEY` 必须明确失败，禁止公共默认加密密钥；不得自动生成替换已有主密钥、修改 `.env` 或迁移生产密文。
- 弃用的 `/onebot/webhook` 只返回兼容响应，不读取或缓存请求体。
- 依赖安全基线和审计证据见 `docs/security-review-2026-10-22.md`；前端使用官方 npm registry 审计，镜像审计接口失败不得当作零漏洞。安装环境中的工具链也需更新，审计原始报告保存在忽略的 `data/` 中。

## 新闻监听诊断

- `/newswatch logs [@账号]`（兼容 `/tibowatch logs`）仅限管理员，沿用各自命令开关；`retry` 只提前重试加密待发送项并使在途工作失效，不清除基线、去重或绕过审核。
- WebUI 监听面板展示最近 300 条有界阶段日志、当前轮次、耗时、退避与待发送队列，可导出 JSON；`/api/tibo-watch/logs` 与 `/retry` 继续要求 WebUI 鉴权。
- 日志仅允许固定事件、账号、帖子 ID、通知对象、计数、时间和耗时；严禁正文、译文、任意异常消息、模型回复、凭据或来源完整 URL。拦截事件只保留计数、时间和固定分类原因。
- 抓取启动与完成时间分开；详情及翻译总超时 120 秒、审核 60 秒、发送确认 30 秒、整轮 600 秒，超时保留加密待发送项。日志写入不得提前提交或拆开新闻 ACK 与总结计数的原子事务。

## 新闻总结与事件记忆

- 独立总结默认关闭，每对象成功送达 10 条自动新闻触发，阈值 1–100；失败、重复、手动 `/tibo` 和总结本身不计数。计数与新闻发送确认同事务保存，不回放开启前历史。
- 总结由 `news_summary.py` 独立调度，每轮最多处理两个对象；素材、输出与任务正文加密，跨重启分页续传，模型 / 配置 / 记忆变化使在途结果失效，已经发送的页面不得重发。
- `/summarymodel` 从监测配置选模型，`default` 跟随审核模型，不允许静默替换失效配置。`/newssummary` 管理开关、条数、最长等待、时间线、记忆、手动排队、历史与清空；两者均仅限管理员并受命令开关控制。
- 总结、记忆和来源按通知对象隔离。历史只检索该对象成功总结过的已审核新闻，保留期、条数、上下文和积压容量有界；清空历史不得误删尚待总结的新帖，移除订阅须清理素材和活动批次。
- Markdown 使用离线 token 渲染，不执行 HTML、脚本、外部图片或模型工具调用。时间线日期取真实来源；所有正文、事件和关联必须引用已提供来源，禁止凭空补充事实与链接，历史不得伪装成本期。
- 总结及引用片段必须通过 `news_filter.review_payload`；失败保留加密任务重试，明确拦截只保留固定原因与计数等元数据。文档在 `docs/ai-news-summary.md`，离线验收脚本不得调用真实模型或 OneBot。

## X / RSS 多源与晚到帖子

- 默认来源池位于 `backend/app/news_sources.py`，FxTwitter 与三个实测可用的 Nitter 实例并行合并，不能在第一个来源成功后停止。FixupX 不算独立上游；来源记录维护在 `docs/ai-news-sources.md`。
- 429 / 503 跨账号退避并尊重 `Retry-After`；抓取并发、超时、缓存容量必须有界。健康状态只保存域名、固定状态、条数、帖子 ID 与退避时间，不保存响应正文或带凭据的 URL。
- 同域名请求启动间隔至少 1 秒、最多两个在途请求；普通错误退避上限 300 秒，429 / 503 使用独立域名计数，不得继承失败账号的历史次数。上游明确的 `Retry-After` 可以超过 300 秒，必须遵守。来源状态保留固定原因和账号 / 域名作用范围，不输出任意异常。
- 按账号保存初次基线与最近 2048 个已处理 ID，晚到帖子可小于移动游标；不得重复投递、重复计入内容拦截或回放初次基线前的历史。旧版本以原游标为安全基线。新发现项仍须严格审核。
- `TIBO_RADAR_SOURCE_URLS` 是最多 12 项的 JSON 数组，支持 `{username}`；单一来源设置继续兼容。`scripts/check_news_sources.py` 只读诊断，不使用 X Token、不读数据库、不调用审核 / 翻译或发送通知。

## 新闻模型选择与 emoji

- `/reviewmodel`、`/translatemodel` 为管理员配置命令，各有开关，支持 `list/status/配置ID或完整名称/default`；只存监测配置 ID，不在聊天暴露地址或密钥。审核未单独选择时继续复用 `intel.llm`，`reviewmodel off` 必须拒绝。
- 模型请求维护 `auto/openai/anthropic` 与配置级 TLS，OpenAI 不带输出 token 上限。失效或停用的所选配置不得静默切换。选择变化须使在途图片失效，重试审核/翻译异常的加密待发送项。
- 翻译通过任务局部上下文覆盖原帖、背景和评论，不修改共享客户端状态。大模型失败不回退到公共机器翻译；原样保留账号、URL、emoji，最终译文继续严格审核。
- 报告 emoji 采用离线 Twemoji 图集，统一测宽与绘制、保持组合表情完整；素材由 `scripts/build_emoji_assets.py` 构建，必须保留归属与 CC BY 4.0 许可证。不得为渲染帖子临时下载外部图片。

## AI 消息严格内容过滤

- 自动推送与手动 `/tibo` 都必须先过滤再发送，不提供关闭或白名单豁免。规则位于 `backend/app/news_filter.py`，政治及高风险内容整帖跳过；只允许审核明确通过的 AI 技术和产品内容。
- 审核优先使用 `/reviewmodel` 选择的监测配置；未单独选择时复用 `intel.llm`，无需启用群聊采集。未配置、超时、模型异常、无效响应和不确定判断不得发送。服务异常保留加密待发送项并重试；明确被拦截的帖子删除所有目标的待发送项，游标继续前进。
- 本地规则在翻译前检查，发送前再次覆盖原文、译文、作者、链接、上下文和评论；旧版本待发送记录必须重审。外部头像不下载、不进入发送图片，未审核附件不转发。
- 拦截审计只保存计数、时间和固定分类原因，不保存正文或模型自由文本。界面必须说明审核模型缺失时暂停投递；不能宣称保证通过中国大陆全部法律或平台审核。

## AI 多账号消息推送

- 2026-10-21 官号修正：内置 `xai` → `spacexsi`、`copilot` → `msftcopilot`，以官网社交链接核验；启动时一次性仅替换已有所选项，保持开关、目标与其他账号进度，不自动扩大订阅。旧账号素材和待发送项按移除规则清理，新账号首次建立基线；已有新账号进度保留。迁移标记与设置同事务提交，不将该映射用于帖子作者校验。

- WebUI「监控管理 → AI 消息监听」在原 Tibo 监听上扩展，内置账号位于 `backend/app/news_accounts.py`，按公司、产品、负责人、创始人分类；自定义账号只接受 X 用户名，不接受任意 URL。
- `/newswatch` 支持通知对象、`on/off/status/accounts`、`recommend/all`、`add/remove @账号`；`/tibowatch` 保留兼容并共用设置，两个命令分别受开关控制。
- 新安装预选推荐组合但监听默认关闭；旧配置缺少 `accounts` 时保留 Tibo-only，不自动扩大已有实际推送范围。新增或移除后重加账号均首次建立基线；移除账号或目标要清理对应待发送项。
- 游标、抓取错误与待发送进度按账号隔离；每轮最多抓取 12 个到期账号、并发 4 个，最多发送三条帖子。设置变化要使在途工作失效，避免重新写回已删除订阅。
- 图片显示真实作者与来源，不将其他账号标成 Tibo，不凭空绘制认证徽章。账号推荐与来源维护在 `docs/ai-news-accounts.md`。

## Tibo 消息监听

- 监听默认关闭，WebUI「监控管理 → AI 消息监听」及 Bot 管理员命令 `/tibowatch G群号&PQQ号` 共用 SQLite 设置；支持 `on/off/status`，命令本身受命令开关控制。
- 默认 120 秒检查，最小 60 秒；初次只建立基线，之后对订阅源中的多条新帖逐条翻译、渲染和推送。
- 游标与加密待发送内容一起持久化，按帖子和通知对象分别确认、退避重试，重启后续传。每轮最多处理三条帖子。移除目标后停止向其投递，新增目标只接收此后发现的帖子。
- UI 验收使用隔离预览和模拟发送；不得在测试中向真实群聊或私聊发送通知。

## 2026-10-19 Tibo 雷达更新

- `/tibo` 每次调用时读取 X `@thsottiaux` 的账号时间线，按发布时间选择最新原创或回复，排除转帖，不以置顶顺序判断最新。
- 支持 X API v2 Bearer Token，以及公开时间线或可配置 RSS/Atom/JSON 来源。旧 Codex Radar 来源配置自动转为新时间线默认值。
- 图片只展示帖子原文、中文翻译及可选回复上下文，不再读取或展示区域、概率或位置判断。翻译失败时明确标注不可用，不拿其他摘要冒充译文。

## 2026-10-19 价格支持更新

- `/price` 同时支持 Sub2API 与 New API。New API 模型广场使用 `/api/pricing`，按 `enable_groups` 过滤模型，支持 Token、计次及单档线性表达式价格；不能可靠换算的动态规则明确提示，不使用旧倍率字段推测价格。
- New API 按站点显示币种设置换算，设置不可用时标注 USD；Sub2API 继续沿用其原有计价约定。
- 外部状态页截图命令已删除，不再维护截图模块、浏览器配置或相关命令开关。

## 2026-09-25 移植后的约束

以下要求优先于本文旧版描述中的冲突条目：

- 仅维护独立 APIMonitorBot，不引入 upstream-ops 网关观测/调度或 GPT-5.6 掺水检测。
- `auto/openai/anthropic` 协议均需维护。OpenAI 请求不得带输出 token 上限；Anthropic Messages 使用其必需的 `max_tokens`。
- TLS 保留旧配置关闭校验的默认值，但允许按配置主动开启，不得强制切换旧配置。
- 成功率为滚动 24 小时，保留旧 `today_availability` 字段名以兼容客户端。
- 定时超时只写独立 `probe_observations`，不影响成功率和业务通知。分钟级显示超时，聚合窗口在超时占比严格大于 40% 时显示。
- 所有自产 PNG 使用 `material_theme.py`：逐次改变主题色，北京时间 07:00–19:00 浅色，其余深色。状态/涨跌语义固定。
- 群聊情报仅按群白名单采集，默认关闭，私聊及凭据输入步骤不得采集。上下文、详情和多轮会话必须加密；发送审计不得保存情报明文。
- 公开状态页默认关闭，逐分组选择可见性，仅匿名开放 `/api/public-status`；其余新增 API 继续要求 WebUI 鉴权。
- 新增回归测试在 `tests/test_monitor_port.py`。离线图片验收使用 `scripts/render_theme_samples.py`；UI 验收使用 `scripts/preview_monitor.py` 与 `scripts/smoke_monitor_ui.cjs`，不得连接真实 OneBot 或生产探测配置。

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
- 普通用户调用 `/check`、`/status`、`/price`、`/radar`、`/tibo` 有 5 分钟冷却。
- 群聊普通用户只能操作或查看绑定本群通知对象的配置；配置支持多个通知对象，例如 `G123456789&P1122334455`。
- 私聊命令默认只允许管理员，除非后续明确开放。

## 命令系统

必须保留并维护这些命令：

- `/addapi`：管理员专用，多轮对话添加 API。
- `/list`：管理员专用，列出所有配置。
- `/remove <apiname>`：管理员专用，删除配置。
- `/check [apiname]`：立即检查配置；不带参数时检查当前通知对象绑定的所有 API，命中多条时用图片渲染结果，避免发送过长文本。
- `/status [apiname]`：生成 API 状态条 PNG 并发送。
- `/addsub2`：管理员专用，多轮对话添加 Sub2API 渠道倍率监控。
- `/price`：发送当前通知对象绑定的 Sub2API / New API 价格表图片。
- `/up`、`up`：对全 Bot 整体 Token 倍率投看涨票；`/down`、`down`：投看跌票。每个 QQ 每个上海自然日一票，可在当天改票。
- `/radar`：读取 Codex Radar 公开摘要并发送模型 IQ 降智趋势图。
- `/tibo`：读取 X `@thsottiaux` 最新公开帖子，发送带中文翻译的雷达图，使用统一日夜主题。
- `/tibowatch [G群号&PQQ号|on|off|status]`：管理员配置 Tibo 新帖自动推送。
- `/newswatch`：管理员配置 AI 多账号推送；支持 `all/recommend/accounts`、`add/remove @账号` 和通知对象、开关及状态查询。
- `/reviewmodel`、`/translatemodel`：管理员选择审核与翻译使用的监测配置，支持 `list/status/default`。
- `/summarymodel`：管理员选择新闻总结模型，支持 `list/status/配置ID或名称/default`，默认跟随审核模型。
- `/newssummary`：管理员配置新闻总结开关、`count/wait/days`、`memory/timeline/connections`、`now/history/clear`。
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
  - `/price` 的 Sub2API 模型价格只能来自 `/api/v1/channels/available`，New API 来自 `/api/pricing`，不得使用内置静态模型价目表。
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
  - 命令开关，位于 Sub2API 渠道倍率面板下方，允许开启或关闭 `/addapi`、`/addsub2`、`/list`、`/remove`、`/check`、`/status`、`/price`、`/up`、`/down`、`/radar`、`/tibo`、`/tibowatch`、`/newswatch`、`/reviewmodel`、`/translatemodel`、`/summarymodel`、`/newssummary`。
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
