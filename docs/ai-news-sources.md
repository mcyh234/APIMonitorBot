# X / RSS 来源维护

核对日期：2026-10-20。公开来源随时可能缓存、限流或停用，以下是本机实测结果，不代表持续可用或后端完全独立。来源发现参考 [Nitter 官方实例目录](https://github.com/zedeus/nitter/wiki/Instances)。

| 默认来源 | Tibo 订阅地址 | 实测 |
| --- | --- | --- |
| FxTwitter | `https://fxtwitter.com/thsottiaux/feed.xml?count=100&with_replies=1` | HTTP 200，可解析，61 条；此前 count=30 时返回 18 条 |
| Nitter Kareem | `https://nitter.kareem.one/thsottiaux/with_replies/rss` | HTTP 200，可解析，20 条 |
| Nitter Jayden | `https://nitter.jaydenha.uk/thsottiaux/with_replies/rss` | HTTP 200，可解析，20 条 |
| Nitter Netbub | `https://nitter.netbub.com/thsottiaux/with_replies/rss` | HTTP 200，可解析，20 条 |

各账号替换 URL 中的用户名，同时查询全部来源并合并帖子。FxTwitter 与 FixupX 是同源别名，FixupX 不计作独立备用。多实例只能减少单站故障，不能保证覆盖 X 的全部消息或全部账号。

2026-10-21 更新：同一来源域名的请求启动间隔至少 1 秒，最多两个在途请求，降低多账号集中请求触发限流的风险，同时避免慢响应长时间挡住后续账号。单次网络请求最多 5 秒，含排队总等待最多 18 秒，账号轮次仍受 20 秒上限约束。普通网络、解析及 HTTP 错误按 60、120、240、300 秒退避，不再因重复失败自行延长到 1 小时。429 / 503 使用独立的域名失败计数并跨账号退避，不继承某个失败账号的历史次数；上游明确返回的 `Retry-After`（秒数或 HTTP 日期）仍完整遵守，可能超过 5 分钟。其他 HTTP 错误只影响当前账号的该来源。

状态新增固定原因与范围：`scope=account/host` 区分账号来源和整个域名，`reason=retry_after/http_error/timeout/network_or_parse` 区分上游指定等待、HTTP 错误、超时、网络或解析失败；来源池满时使用 `queue_full`，不会无限扩展缓存。冷却期间保留这些原因与原 HTTP 状态；不记录异常正文或完整 URL。等待不会在每次检查时重新计时。检查间隔和账号轮转仍可能使实际重试晚于冷却截止。

其他候选实测：nitter.net 返回 410，xcancel.com 返回 451；nitter.space、nitter.xitter.cc、nitter.tiekoetter.com、nuku.trabun.org 返回 403；nitter.privacydev.net 和 RSSHub 官方 Twitter 路由返回 404；rsshub.rssforever.com 返回 503；其他部分实例连接失败。未通过实测的来源不加入默认池，也不绕过访问限制。

可在 `.env` 配置自己的多源池，例如：

```dotenv
TIBO_RADAR_SOURCE_URLS=["https://rss.example/x/{username}","https://another.example/{username}/with_replies/rss"]
```

最多 12 项；带 `{username}` 的模板用于全部账号，固定地址仅用于 Tibo。已有 `TIBO_RADAR_SOURCE_URL` 保持兼容，非空时也会加入自定义池。两项都留空时使用内置池。修改后重启服务。自托管 RSSHub 需要按其上游路由要求配置，不能把任意公共 RSSHub 实例视为可用 X 来源。

## 漏报排查

在 WebUI 展开「监控管理 → AI 消息监听 → 各账号抓取状态」，先区分抓取失败、来源限流、待发送、缺少审核模型和内容拦截。只有明确审核通过的 AI 内容才会发送；增加 RSS 不会绕过审核。

可执行只读诊断（不会翻译、调用审核模型或发送消息，不输出帖子正文和凭据）：

```powershell
.\.venv\Scripts\python.exe scripts/check_news_sources.py --username thsottiaux
.\.venv\Scripts\python.exe scripts/check_news_sources.py --username thsottiaux --post-id 你的帖子ID
```

结果包含各来源状态、帖子 ID、发布时间、原帖链接，以及指定帖子是否出现在当前可获取的时间线中。`--source` 可重复指定来源做对照。诊断只测公开 RSS，不使用 X Token，也不读取数据库。

监听使用初次基线和最近 2048 个已处理 ID 处理乱序、重复与晚到消息；旧版本首次升级以原游标为安全基线，不回放已处理历史。所有来源都遗漏的帖子、超过来源保留范围的帖子，以及升级前已越过的漏帖，仍可能无法自动补齐。
