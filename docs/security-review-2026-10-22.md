# 2026-10-22 安全排查与修复

范围：仓库代码、Python 虚拟环境、前端锁文件和构建依赖。对照截至 2026-10-22 的维护者公告及官方漏洞数据库，使用隔离审计工具、临时文件和模拟 ASGI 请求验证；不探测生产接口、不向 OneBot 发送消息、不读取真实密钥或数据库正文。

## 已确认的项目问题

| 问题 | 验证与影响 | 修复 |
| --- | --- | --- |
| 未鉴权的前端路由目录越界读取 | 使用临时目录的 `outside-canary.txt` 和编码路径，修复前得到 HTTP 200。路由拼接未检查目录边界，可能暴露运行账号可读取的配置、密钥或数据库 | 在文件访问前拒绝遍历、盘符、UNC、设备名、ADS、点文件和 Windows 路径别名；逐段拒绝符号链接与重解析点，并检查构建目录边界。首页与合法客户端路由继续可用 |
| 空主密钥使用公共固定回退密钥 | 旧 `SecretBox("")` 可由源码中的固定值推导出加密密钥，缺少独立部署密钥时无法提供有效的密文隔离 | 空白主密钥明确报错；保留已有非空密钥及 Fernet 格式，不自动改写 `.env`、数据库或现有密文 |
| 弃用 webhook 无界读入请求体 | 公开兼容端点原先调用 `request.body()`，对业务无用的大请求仍占用应用内存 | 直接返回兼容响应，不读取请求体 |
| 畸形令牌引发异常 | 超长时间戳可能触发整数转换错误，非 ASCII 签名可能触发字符串比较异常 | 在解析和签名比较之前限制总长度和 ASCII 格式，畸形令牌正常返回未授权 |
| 上游回显探测凭据 | 上游错误正文或成功消息可回显 APIKey，原探测结果可能将其作为错误或预览保存、显示 | 对 OpenAI / Anthropic 探测结果先替换当前请求密钥再截断；异常结果也去除该密钥，保持原有探测状态语义 |

这些为项目自身缺陷，不冒用第三方 CVE 编号。文件读取只在本地临时测试目录中复现，未尝试读取实际 `.env` 或 SQLite 内容。若旧版本曾开放给不可信访问者，应结合部署日志评估暴露情况；本次代码审计不能证明是否曾发生泄露。

## 近期公告与依赖核对

审计初始结果：官方 npm audit 报告 **6 个包受影响**（5 high、1 moderate）；Python pip-audit 报告 **6 个包、50 条命中记录，按公告 ID 去重为 39 项**。重复条目不是独立漏洞；包版本命中也不等于攻击条件已在本项目满足。

| 包 | 审计前 | 验证修补版 | 说明 |
| --- | --- | --- | --- |
| Vite | 7.3.6 | 7.3.7 | 命中 Windows launch-editor 及开发服务器公告；前端正常生产部署使用 FastAPI 静态构建，不运行 Vite 开发服务器 |
| postcss | 8.5.16 | 8.5.31 | sourceMappingURL 路径处理公告，属于构建工具链 |
| nanoid | 3.3.15 | 3.3.20 | 特殊 size 参数导致循环的公告，属于间接依赖 |
| browserslist | 4.28.4 | 4.29.4 | 缓存无界与不可信自定义统计公告，属于构建工具链 |
| baseline-browser-mapping | 2.10.40 | 2.11.30 | 非法输入导致进程终止公告，属于构建工具链 |
| source-map-js | 1.2.1 | 1.2.2 | 索引 source map 解析 DoS 公告 |
| AnyIO | 4.14.1 | 4.15.1 | TLS/IDNA 等公告；依赖要求提高至至少 4.14.2，保留原有配置级 TLS 行为 |
| certifi | 2026.6.17 | 2026.10.19 | 更新信任证书包 |
| cryptography | 49.0.0 | 50.0.2 | PKCS#7 解密 oracle 公告；项目使用 Fernet，未发现使用该 PKCS#7 解密接口，仍更新依赖 |
| Pillow | 12.3.0 | 13.0.0 | 多个格式解析公告；当前自动新闻不下载外部头像，主要加载离线素材与自产 PNG，仍更新依赖并验证渲染 |
| pip / setuptools | 24.0 / 65.5.0 | 26.2.2 / 84.2.1 | 安装及打包工具公告，非 Web 路由漏洞；requirements 明确工具链安全下限，升级器在本机安装依赖时一并更新 |

表中前端初始版本以审计时锁文件为准，修补版本通过新锁文件固定；后端记录为实际测试环境版本，不表示生产部署已经安装。`requirements.txt` 提高 AnyIO、证书包、cryptography、Pillow、pip、setuptools 的安全下限，并显式要求 Starlette 至少 1.3.1。安装器还解析到了较新的 FastAPI、Starlette、Uvicorn、SQLAlchemy 等兼容版本，整体运行回归一并验证。

另核对以下公告：

- [Vite Windows launch-editor，CVE-2026-112942 / GHSA-587h-7wmj-h5pm](https://github.com/advisories/GHSA-587h-7wmj-h5pm)：修补版含 Vite 7.3.7。本项目在 Windows 开发时存在相关组件，已更新。
- [Vite 跨源脚本执行，GHSA-9jrq-w75r-8gcw](https://github.com/vitejs/vite/security/advisories/GHSA-9jrq-w75r-8gcw)：公告要求 custom/middleware 模式等条件；本项目使用普通 SPA 开发配置，未发现满足这些条件，仍通过更新消除版本命中。
- [2026-10-21 新公布的 Vite v6 查询绕过，GHSA-8g5g-mxqh-grgj](https://github.com/vitejs/vite/security/advisories/GHSA-8g5g-mxqh-grgj)：仅影响 6.4.4，本项目使用 7.x，不受该条影响。
- [Starlette Host 重建导致鉴权路径错位，CVE-2026-48710](https://github.com/Kludex/starlette/security/advisories/GHSA-86qp-5c8j-p5mr)：初始安装的 Starlette 1.3.1 已含库修复；应用仍由 `request.url.path` 改为 ASGI `scope["path"]`，防止鉴权与实际路由的路径来源不同。没有把它描述为当前库版本下已复现的鉴权绕过。
- [Starlette Windows StaticFiles UNC / NTLM，GHSA-wqp7-x3pw-xc5r](https://github.com/Kludex/starlette/security/advisories/GHSA-wqp7-x3pw-xc5r)：库修补于 1.1.0，初始版本已修补。项目自身 SPA 路由的 Windows 路径处理另行修复，不依赖静态文件库兜底。
- [AnyIO TLS / IDNA，GHSA-82r6-8w77-94w6](https://github.com/agronholm/anyio/security/advisories/GHSA-82r6-8w77-94w6)、[cryptography PKCS#7，GHSA-g6cj-pr64-35w5](https://github.com/advisories/GHSA-g6cj-pr64-35w5)、[Pillow PDF 递归，GHSA-p4wg-p73j-c528](https://github.com/advisories/GHSA-p4wg-p73j-c528)：作为依赖公告记录，具体攻击面按上表区分。

## 其他审查边界

- React 正常文本插值与新闻 Markdown 离线 token 渲染均不执行模型 HTML、脚本或工具调用；未发现用于帖子正文的 `dangerouslySetInnerHTML`。
- 新增业务 API 继续受 WebUI 鉴权，公开状态页只暴露已选择的分组。升级包只接受登录后上传的受信任代码包，SHA-256 提供文件完整性，不是发行者签名。
- 探测地址、OneBot、RSS 和模型 API 按项目用途允许管理员配置，包括本机及内网服务；没有把合法内网监控当作应全面禁止的 SSRF。原有配置级 TLS 兼容行为保留。
- 未改变新闻的严格审核、加密队列、去重、摘要引用与通知限制；未将生产网络连通或真实群聊发送作为验收手段。

## 安装、复扫与验证

先备份已有 `.env` 与数据库，保留原有有效主密钥。曾以空主密钥生成密文的部署需要单独迁移，不能直接填新密钥后期望旧密文可解密。

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
cd frontend
npm ci
npm run build
npm audit --registry=https://registry.npmjs.org
cd ..
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pip_audit
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m compileall -q backend run.py scripts tests
```

镜像 registry 本次不提供 audit API（404 / NOT_IMPLEMENTED），已改用官方 registry 取得有效结果，未把镜像报错当作无漏洞。Python 审计工具安装于隔离目录，仅扫描应用虚拟环境；原始前后报告位于忽略的 `data/`，没有提交环境文件、数据库或密钥。

更新后扫描：npm audit 和 Python 应用环境 pip-audit 均无已知漏洞命中，`pip check` 通过。此结论只覆盖当次数据库可识别的版本与所查代码，不代表不存在未知漏洞；操作系统、Python 自带原生库、Node 运行时及反向代理不属于此次包审计的完整覆盖范围。

验收：317 项测试通过；Python compileall、前端生产构建与 TypeScript 检查通过；隔离 WebUI 桌面 / 手机验收通过。新增安全用例覆盖越界读取复现、危险路径访问前拒绝、重解析点、路由路径鉴权、畸形令牌、空主密钥、webhook 不读请求体，以及两种模型协议的凭据回显脱敏。
