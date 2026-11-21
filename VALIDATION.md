# 验证记录 · v1.0

日期：2026-11-22（UTC+8）。本包是空白模板，尚未替用户创建仓库或部署。

## 已完成

- Python 3.12.16 标准库离线测试：**99 个测试方法，全部通过**。最终一次运行耗时 1.735 秒，无跳过、无 expectedFailure。
- 覆盖：空源、正式/测试发行版选择、精确 glob、多文件歧义、API 凭据隔离、HTTPS/明显本地地址校验、XML/二进制 Info.plist、包大小与 SHA-256、Bundle ID 锁定与去重、构建号两种字段、缓存、直链重取、权限声明外层校验、失败保留旧文件及可处理写入异常回滚；另覆盖手机添加请求的所有者双重校验、公开 HTTPS 和直接 IPA URL 限制、字段完整性、重复项目拒绝，以及候选验证失败不写入配置。
- 所有测试使用内存生成的 ZIP / plist 与模拟网络，测试时清空环境；没有下载或运行真实第三方 IPA，也没有读取真实账号凭据。
- `node --check public/app.js` 通过。
- 发布工作流 YAML 解析、触发计划、构建→部署依赖、Pages 输出路径、环境变量绑定静态断言通过。
- HTML ID 唯一性、本地子资源路径、JSON 空列表结构、自制 PNG 文件头检查通过。
- `python3 scripts/update.py` 对交付空配置成功生成 `apps: []`，无需网络。

## 初轮发现与最终处置

1. 尾点 localhost 与历史数字 IPv4 写法：增加归一化/拒绝规则，并通过对应回归。仍不是完整 DNS 重绑定防护，配置需可信。
2. 生成末尾文件写入异常可能留下部分本地更新：加入可捕获异常的尽力回滚，并通过模拟写入失败测试。不是掉电一致性数据库；真正上线由成功后的独立 Pages 部署步骤完成。
3. 初轮测试曾要求“找不到 IPA 就跳过”；最终明确采用更保守的**失败阻止发布、保留线上旧源**策略，更新了两项测试契约与 README。没有用跳过测试或 expectedFailure 隐藏问题。
4. 根据官方解析代码，构建号同时输出 `buildVersion` 与 `buildNumber`，且缺少真实 CFBundleVersion 时不臆造。完整权限不自动推断；可由维护者显式提供，未提供时不伪装成空权限。

## 未完成或未验证

- 没有在 LiveContainer / SideStore / AltStore 实机导入或安装测试。
- 没有实际创建仓库、运行 GitHub Actions 或部署 Pages；YAML 可解析不等于已完成线上部署。
- 浏览器工具曾成功导航到首页，但后续截图/导航无响应，故**没有完成视觉和浏览器交互验收**；已执行上述静态资源/脚本语法检查。可自行打开 `public/index.html` 预览。
- 官方 LiveContainer 3.8.0 与主分支解析器的字段/空源/URL scheme 已静态核对；不保证未来或旧版本全部行为相同，依据链接见 README。

检查并非安全审计，也不为未来收录的任何 IPA 背书。

## 手机添加入口补充验证 · v1.2

- 新增首页“添加应用 +”按钮及 `public/add.html` / `add.js`：HTTPS 项目地址、IPA glob、预发行版选项；无 token、无嵌入凭据。GitHub Issue URL 参数由标准 `URLSearchParams` 生成，须在 GitHub 登录后人工确认提交；请求内容公开。
- 新增 `scripts/add_request.py` 与 `.github/workflows/add-ipa.yml`：只接受仓库所有者 `xx123456789xx` 的 issue 作者和 sender、固定标题和固定字段；请求都作为数据解析，不用于 shell 命令拼接。对候选配置运行生产更新器完整校验，失败不写 config。有效时提交 config，显式触发 `workflow_dispatch` 发布；Issue 自动留言并关闭。
- 工作流权限精确设定 `contents: write` / `actions: write` / `issues: write`；不向 Pages 注入 PAT。公开直接 IPA 仅接受 HTTPS、`.ipa` 直链，不接受含 query/fragment 的临时签名地址；GitHub 仓库则检查发布资产、实际 IPA 元数据与 Bundle ID。
- 手机 Safari 上的 GitHub issue URL 预填交互、正式 Issue 创建回路和真实有效 IPA 端到端尚未操作验证；用户可用自己的公开测试 Release 触发真实测试。部署工作流 YAML 和 owner gate 已静态解析校验通过；99 个本地离线测试全部通过。
- 安全模型仅支持该仓库所有者提交；Issue 是公开的且要求 GitHub 登录。不要输入秘密 URL 或登录凭据。
