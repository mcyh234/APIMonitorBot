# APIMonitorBot

APIMonitorBot 是一个本机运行的 OpenAI 兼容 API 可用性监视器。它会定时探测多个 `BaseURL + APIKey + 模型` 配置，在服务中断、持续未恢复、恢复可用时，通过 OneBot v11 向 QQ 群聊或私聊推送通知。

GitHub 项目地址：[mcyh234/APIMonitorBot](https://github.com/mcyh234/APIMonitorBot)

项目包含 FastAPI 后端、React WebUI、SQLite 数据库、OneBot WebSocket 接入、管理员命令系统、多轮添加配置、密钥加密存储、状态图、网页快照、Sub2API/NewAPI 倍率监控和本地升级管理。

所有项目文件均按 UTF-8 维护。

## 功能概览

- 监控 OpenAI 兼容 `POST {BaseURL}/chat/completions` 接口。
- 支持多个 API 配置，每个配置可绑定一个或多个 QQ 群/私聊通知对象，例如 `G123456789&P1122334455`。
- OneBot v11 WebSocket 收发消息，支持 token header，也可兼容 query token。
- SQLite 持久化，APIKey 使用 `SECRET_MASTER_KEY` 加密后入库。
- 默认管理员 QQ：`2087900785`，WebUI 可维护多个管理员。
- 完整命令开关与别名系统，管理员命令、普通查询命令和投票命令分别执行权限校验。
- `/addapi` 支持多轮对话添加 API，APIKey 强制在私聊内收集。
- `/addsub2` 支持多轮对话添加 Sub2API 渠道倍率监控。
- `/price` 输出当前通知对象绑定的 Sub2API 价格表图片。
- `/up`、`/down` 或裸词 `up`、`down` 参与全 Bot 当日整体 Token 倍率看涨/看跌投票。
- `/radar` 读取 Codex Radar 公开摘要，输出模型 IQ 降智趋势和最新配置排名图片。
- `/tibo` 读取 Tibo 最新公开 X 帖子和 presence 摘要，输出原文、中文翻译及粗粒度动态雷达图。
- 定时巡检失败会二次确认，减少偶发波动误报。
- Sub2API 每分钟检测渠道分组 `rate_multiplier`，倍率变化或分组被删除时发送高亮图片，并追加文字通报说明变化分组。
- Sub2API 登录 token 加密长效存储，token 失效后才重新登录。
- 同一通知对象同一轮多 API 状态变化会合并报告，防止刷屏。
- `TIMEOUT` 和 `NETWORK_ERROR` 不计入业务中断通知。
- Timeout 时会额外检查 Google 连通性，如果国际网络也断开，会私聊通知默认管理员。
- WebUI 支持配置 CRUD、启停、手动检查、巡检历史、最近接收消息、发送失败原因、管理员管理。
- WebUI 首次进入设置访问密钥，后续所有业务 API 均要求登录 token。
- WebUI 状态条图展示最近 30 分钟、5 小时、24 小时接口状态。
- WebUI 支持拖拽上传升级包、一键安装并自动重启，也可以直接生成当前版本的升级包。
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
git clone https://github.com/mcyh234/APIMonitorBot.git
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

OneBot 可以稍后在 WebUI 的“快速上手”里配置；推荐 NapCat 只开启 WebSocket Server。

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

首次进入 WebUI 会要求设置一个“进入密钥”，可以点击“自动生成”。保存后进入快速上手面板，按提示完成 NapCat WebSocket、管理员和 API 配置。全部完成后快速上手面板会自动隐藏。

如果忘记 WebUI 进入密钥，可以在项目根目录运行脚本重置：

```powershell
.\.venv\Scripts\python.exe scripts\reset_webui_secret.py
```

默认会清除当前密钥，刷新 WebUI 后回到首次设置页面。也可以直接设置新密钥：

```powershell
.\.venv\Scripts\python.exe scripts\reset_webui_secret.py --secret "new-webui-secret"
```

或者自动生成并保存一个新密钥：

```powershell
.\.venv\Scripts\python.exe scripts\reset_webui_secret.py --generate
```

## 常用配置

`.env.example` 已包含完整示例。常用项如下：

```env
APP_HOST=127.0.0.1
APP_PORT=8000
APP_TIMEZONE=Asia/Shanghai
DATABASE_URL=sqlite:///./data/apimonitor.sqlite3

ONEBOT_WS_URL=ws://127.0.0.1:3001
ONEBOT_ACCESS_TOKEN=
ONEBOT_WS_TOKEN_IN_QUERY=true

DEFAULT_ADMIN_QQ=2087900785
CHECK_INTERVAL_SECONDS=60
NIGHT_SAVER_ENABLED=true
NIGHT_SAVER_START_HOUR=0
NIGHT_SAVER_START_MINUTE=0
NIGHT_SAVER_END_HOUR=8
NIGHT_SAVER_END_MINUTE=0
NIGHT_SAVER_INTERVAL_SECONDS=600
COMMAND_CHECK_COOLDOWN_SECONDS=300

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

推荐使用 NapCat WebSocket Server：

1. 在 NapCat 的网络配置里开启 WebSocket Server。
2. 监听地址建议 `127.0.0.1`，端口建议 `3001`。
3. 在 NapCat 上设置 token。
4. 在 APIMonitorBot WebUI 的“快速上手”中填写 `ws://127.0.0.1:3001`，复制 NapCat 上配置好的 token，并勾选 query token。
5. 保存后 WebUI 会自动重连。

APIMonitorBot 只通过 WebSocket 连接收发 OneBot 事件和 action，例如 `send_group_msg`、`send_private_msg`、`get_group_list`。旧版 OneBot HTTP 路径已弃用，新部署只需要配置 WebSocket Server 和 NapCat token，避免重复触发命令或重复发送。

## 机器人命令

- `/addapi`：管理员专用，开始多轮对话添加 API。
- `/addsub2`：管理员专用，开始多轮对话添加 Sub2API 渠道倍率监控。
- `/list`：管理员专用，列出所有配置。
- `/remove <apiname>`：管理员专用，删除指定配置。
- `/check [apiname]`：立即检查指定配置；不带参数时检查当前通知对象绑定的全部 API，并用图片汇总结果。群聊只能查绑定本群的配置，私聊仅管理员可用。
- `/status [apiname]`：发送 API 状态条 PNG。群聊无参数时显示本群绑定的所有配置，私聊管理员可看全部。
- `/stat`：抓取 GPTStore 状态页全页快照，切到 1 小时请求成功率视图后发送图片。
- `/price`：发送当前群聊或私聊通知对象绑定的 Sub2API 渠道倍率图片；配置可用 `&` 同时绑定多个通知对象。
- `/up`、`up`：看涨整体 Token 倍率；`/down`、`down`：看跌。每个 QQ 每个上海自然日一票，当天可以改票。
- `/radar`：读取 `codexradar.com/current.json` 公开摘要，发送最近 7 个测试批次的模型 IQ 趋势图。
- `/tibo`：从 `tibo_presence.source_urls` 获取最新公开 X 帖子，生成白底 X 风格中英双语图片。
- `/cancel`：取消当前多轮对话。

普通用户调用 `/check`、`/status`、`/stat`、`/price`、`/radar`、`/tibo` 有 5 分钟冷却；管理员不受冷却限制。

`up/down` 投票不使用 5 分钟命令冷却。每个 QQ 在每个上海自然日只有一条有效投票记录，重复同方向保持不变，发送相反方向会修改当天投票。

WebUI 的“命令开关”可以开启或关闭 `/addapi`、`/addsub2`、`/list`、`/remove`、`/check`、`/status`、`/stat`、`/price`、`/up`、`/down`、`/radar`、`/tibo`。`up`、`down` 是对应投票命令的永久默认别名，`/cancel` 始终保留。

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

Sub2API 渠道倍率监控：

- 登录接口：`POST {BaseURL}/api/v1/auth/login`。
- 渠道接口：`GET {BaseURL}/api/v1/groups/available`。
- `/price` 额外读取 `GET {BaseURL}/api/v1/channels/available`，使用接口返回的分组倍率和模型 Token 单价。
- 模型单价字段是每 Token 价格：`input_price`、`output_price`、`cache_write_price`、`cache_read_price`。
- Sub2API 按 `1 CNY = 1 USD` 计价单位处理，每 MTok 人民币价格为 `每 Token 单价 × 1,000,000 × rate_multiplier`。
- 记录返回 `data[]` 中每个分组的 `platform`、`name` 和 `rate_multiplier`。
- 首次看到分组、每天首次成功巡检和后续每次倍率变化都会写入倍率历史，用于按上海自然日聚合开盘、最高、最低、收盘。
- 首次添加只保存当前倍率，不发送变动通知。
- 后续定时检测发现倍率变化或分组被删除时，先发送价格变动图片，再追加文字通报说明具体分组。
- 图片中 Anthropic 渠道使用橙色，OpenAI 渠道使用绿色，发生变化的分组高亮。
- `/price` 图片会展开显示所有分组，包含当前倍率、历史折线、最近 30 天倍率日 K，以及从 `/channels/available` 实时计算的输入、输出、缓存写入、缓存读取 CNY/MTok 价格；上涨为红色，下跌为绿色。
- WebUI、`/price` 和自动价格变动图顶部显示全 Bot 当日看涨/看跌比例；无投票时使用灰色状态条。
- token 会加密存储，未过期时复用；失效后自动重新登录。
- WebUI 支持批量导入多个 Sub2API/NewAPI URL，可以先导入地址，再逐项补充账号、密码或 access token。

## WebUI

WebUI 首屏就是监控后台，不做营销页。主要区域：

- 系统概览指标。
- API 配置表。
- Sub2API 渠道倍率面板，默认收起，点击后展开查看当前倍率、历史折线、最近 30 天日 K 和全局投票比例。
- 命令开关，位于 Sub2API 渠道倍率面板下方。
- 新增 API 表单。
- 管理员 QQ 列表。
- 最近发送失败原因。
- 最近接收到的 OneBot 消息。
- 状态条图。
- 单配置巡检历史。
- 版本升级面板：校验升级包、备份旧文件、安装并自动重启，以及生成升级包。
- “添加 API”“管理员”“版本升级”侧栏面板默认收起，可按需展开，折叠状态会保存在当前浏览器。
- WebUI 右上角提供 GitHub 图标入口，可直接打开项目仓库。
- “巡检与冷却”面板可修改夜间省流开关、分钟级开始/结束时间、夜间巡检间隔和普通用户命令冷却时间；保存后立即生效并持久化到 SQLite。

倍率面板中的日 K 线以分组 `rate_multiplier` 为价格口径，按 `Asia/Shanghai` 自然日聚合开盘、最高、最低和收盘。没有采样记录的日期会留空，不会使用前一日价格伪造数据。

## 一键升级与升级包生成

WebUI 右侧“版本升级”面板提供两种操作：

- 将 `APIMonitorBot-upgrade-<版本>.zip` 拖入上传区，校验通过后点击“安装并重启”。
- 输入版本号并点击“生成升级包”，后端会先执行 WebUI 构建，再下载可直接安装的升级包。

安装升级包时会校验应用标识、升级包格式、必要文件、文件大小和 SHA-256。写入前会把被覆盖的旧文件备份到 `data/upgrades/backups/`；如果 `requirements.txt` 发生变化，会自动使用当前 Python 环境安装新版依赖。`.env`、数据库、`data/`、虚拟环境、日志、缓存和本地压缩包不会进入升级包，也不会被覆盖。

安装完成后服务会自动退出并通过 `run.py` 重新启动。重启记录保存在：

```text
data/upgrades/restart.log
```

只安装可信来源的升级包。清单哈希用于发现文件损坏或篡改，不代表发布者身份认证。

也可以在项目根目录通过命令行生成升级包：

```powershell
.\.venv\Scripts\python.exe scripts\build_upgrade_package.py --version 1.1.0
```

输出文件默认位于 `release/APIMonitorBot-upgrade-1.1.0.zip`。如已经手动执行过 `npm run build`，可以跳过重复构建：

```powershell
.\.venv\Scripts\python.exe scripts\build_upgrade_package.py --version 1.1.0 --skip-frontend-build
```

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

当前完整后端测试为 `91 passed`。涉及图片或响应式页面的改动还应实际渲染 PNG，并在桌面和移动 viewport 检查文字、图表和容器边界。

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

生成包含完整源码、测试、Agent 指南和技术交接文档的迁移包：

```powershell
.\.venv\Scripts\python.exe scripts\build_source_package.py
```

输出位于 `release/APIMonitorBot-migration-source-<版本>.zip`，ZIP 本身保持忽略，不提交到 Git。

## 待实现功能

- Telegram Bot 通知适配。
- WebUI 多用户账号和细粒度 RBAC。
- WebUI 完整编辑 Sub2API/NewAPI 地址、凭据和通知目标。
- WebUI 导出巡检历史 CSV。
- 支持更多探测方式，例如 `/models`、Responses API 或自定义请求体。
- 支持按配置自定义巡检间隔、超时时间、恢复阈值。
- 支持通知模板自定义。
- 支持 Docker Compose 一键部署。
- 建立正式 Alembic migration 版本链，替代当前 `create_all + SQLite ALTER TABLE` 兼容迁移。
- 支持 Prometheus 指标暴露。
- 支持更多 OneBot 适配器的图片发送兼容模式。

## 许可证

本项目使用 MIT License，详见 [LICENSE](LICENSE)。
