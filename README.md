# 个人 IPA 聚合源 · 空白模板

一个 Python 标准库实现的 IPA 源生成器：**GitHub Releases / 公开 IPA 直链 → 一个 source.json → GitHub Pages → LiveContainer / SideStore 等支持的源阅读器**。

初始 `projects: []`，不预置或推荐任何应用。不下载、不安装、不运行任何实际第三方 IPA。图标为本模板自制。

## 先看边界

- 本压缩包是**待部署模板**，不是现成的公网订阅地址。
- GitHub Actions 计划每 **30 分钟**检查一次，可手动运行；调度不是实时推送，可能排队、延迟。客户端也需要刷新源，**不会自动替换已安装 App**。
- 仅支持**公开发行的 IPA 文件**。只有源码、需登录下载、私有 Releases、GitHub Actions 临时构建产物不在本模板范围。
- 生成 AltStore 风格 JSON，并保留旧式单版本字段；主要针对 LiveContainer 源功能。已静态核对官方 3.8.0 与主分支解析器，空 `apps: []` 可解析，构建号同时输出 `buildNumber` / `buildVersion`。**没有在你的设备上实测**，也不保证所有旧版兼容。
- AltStore / SideStore 的实际安装还有权限声明等要求。本模板不会从签名或 Mach-O 自动提取完整 entitlements，也不伪造空权限；需维护者按实际 IPA 添加准确的 `app_permissions`，并自行验证安装。不能把“可解析 JSON”等同“跨客户端安装已验证”。
- 收录不代表安全审计，也不保证 App 可在 LiveContainer 中运行。不会编译、破解、重签名或修改 IPA。

## 一、部署到 GitHub Pages

1. 在**你自己的 GitHub 账号**下新建空仓库，例如 `ipa-source`。个人使用可选公开仓库，以方便使用免费 Pages / Actions；私有仓库 Pages 的可用性取决于账号方案。
2. 解压本包，把 `ipa-source-template/` **里面的内容**上传到仓库根目录，不要再套一层目录。请务必包含隐藏的 `.github/workflows/publish.yml`，只上传 `public/` 不会自动更新。
   - 使用 git 上传最稳妥；若用 GitHub 网页上传，请确认 Actions 页面能看到 `Update and publish IPA source` 工作流。缺失时，在网页创建同路径工作流文件并粘贴本包内容。
   - 默认分支支持 `main` / `master`。其他分支名需修改工作流 `push.branches`；定时任务只在默认分支上运行。
3. 仓库 **Settings → Pages → Build and deployment → Source：GitHub Actions**。
4. 编辑 `config.json` 的源名称和 `identifier`。例如把 `io.github.yourname.ipa-source` 改成 `io.github.你的英文账号.ipa-source`；一个源使用一个稳定唯一标识，不要每次更新都改。
5. 打开 **Actions → Update and publish IPA source → Run workflow**。首次提交时，如果还没配置 Pages，自动任务可能失败；设置好后手动重跑即可。
6. 成功部署后，工作流的 `github-pages` 环境会显示你的真实地址，一般是：

   ```text
   首页：https://YOURNAME.github.io/ipa-source/
   订阅：https://YOURNAME.github.io/ipa-source/source.json
   ```

   `YOURNAME` / 仓库名是占位符，必须换成实际值。首页有“复制订阅地址”按钮；**添加的是 source.json，而不是 ZIP、仓库网页或首页 HTML**。

GitHub Actions 会用 Pages 提供的地址自动填入 `SOURCE_BASE_URL`，不必手工改 `base_url`。若绑定自定义域名，应在 Pages 设置绑定后重跑工作流。

> 本地 / 对话里的 `minis://` 预览不是公网地址，不能给 LiveContainer 当网络源。创建或发布仓库需要你本人操作，模板没有自动调用你的 GitHub 账号。

## 二、在手机网页添加项目

打开首页点 **“添加应用 +”**，粘贴公开 GitHub 仓库 / Release 地址，或免登录 `.ipa` 直链；可填写 IPA 文件名匹配规则和是否包含测试版。点继续后会跳转到 GitHub 的预填 Issue 页面，核对内容并点一次 **Submit new issue** 才会触发处理。

为避免把仓库写入令牌暴露在网页，这个静态表单不直接写 GitHub。请求以公开 Issue 提交（请求中的 URL 会在公开仓库可见），自动化只接受仓库所有者 `xx123456789xx` 自己提交的指定格式；访客即使能创建其他 Issue，也不能触发源配置写入。需要已登录 GitHub、仓库 Issues 功能可用。

工作流先验证地址、Release 中是否有匹配 IPA、实际 IPA 包元数据和整个候选源；任一步不通过都不改配置。成功后机器人追加到 `config.json`、触发源重新发布、在 Issue 留言并关闭。直接链接仅接受无需登录且不带查询令牌的公开 `.ipa` URL。刷新 LiveContainer 的固定源即可查看新条目。

> 手动添加仍可通过直接编辑 `config.json` 的 `projects` 数组，后续 Actions 会自动发布。没有 GitHub 账号时，不能从静态网页获得写入仓库的权限。

## 三、添加项目配置字段

在 `config.json` 中把 `projects: []` 改为下列形式。**示例占位符必须替换，不能原样运行。**

```json
"projects": [
  {
    "repo": "OWNER/REPOSITORY",
    "asset_pattern": "*.ipa",
    "include_prereleases": false
  }
]
```

- `repo` 接受 `owner/repo`、GitHub 仓库链接、该仓库的 Releases 页面链接；指定 release tag 的链接也只识别仓库，**不固定该 tag**。
- 默认从 API 返回的最近最多 **100 个发行版**中，按发布时间由新到旧找第一个包含匹配 IPA 的非草稿正式版。新版未发布 IPA 时，可能继续选取较早的有 IPA 版本。
- 同一发行版如果匹配多个文件，会**停止发布**，不会擅自猜测。请精确指定，例如 `"asset_pattern": "*LiveContainer+SideStore*.ipa"`。
- 通配符大小写敏感；最后的 `.ipa` 扩展名检测不区分大小写，但 glob 仍区分。
- `include_prereleases: true` 会把测试版一起参与发布时间排序；不是“只选测试版”。
- 选定资产下载失败时，不会静默降级到其他包。

也可以使用公开直链：

```json
{
  "url": "https://YOUR-HOST.example/path/app.ipa",
  "name": "我的应用",
  "bundle_identifier": "com.example.app"
}
```

每个条目在 `repo` 和 `url` 中只能选一项。更多例子在 `projects.example.json`；这个示例文件**不自动加载**。

### 可选项目字段

| 字段 | 用途 |
| --- | --- |
| `name` | 自定义显示名；省略则读 IPA 的 Info.plist |
| `developer_name` | 自定义开发者显示名；不等于作者身份验证 |
| `description` | 自定义源中描述 |
| `bundle_identifier` | 可选锁定期望的 Bundle ID；不匹配则拒绝发布 |
| `icon_url` | 公开 HTTPS PNG/JPEG 图标地址；不填使用本源通用图标 |
| `enabled` | 设为 `false` 暂停收录，下一次成功发布时移出源 |
| `app_permissions` | 可选的真实权限声明，含 `entitlements` / `privacy` 两个数组；原样输出为 `appPermissions`，结构与内容须遵循客户端官方规范。脚本只校验外层，不认证准确性 |

首次添加时建议先只加一个项目，确认工作流与客户端正常，再扩充。重复 Bundle ID 会停止发布，避免同一 App 多渠道互相覆盖。

## 四、更新、缓存与失败处理

- 脚本读取上游 IPA 的 `CFBundleIdentifier`、版本号、构建号、最低 iOS 版本，并计算实际字节大小与 SHA-256。版本号**不靠猜 Release tag**。
- 下载只用于元数据读取：临时文件结束后释放，不解压到目录、不安装、不执行。实际客户端下载仍直接访问上游，不镜像 IPA。
- GitHub 发行资产按 ID / 更新时间 / 大小 / URL 缓存元数据；相同资产一般不重复下载。缓存失效、被清理或上游替换资产时会重新下载。
- 直链可能在同一 URL 替换内容，所以**每次检查都重新下载**。大包建议用 GitHub Releases，避免每半小时消耗流量。
- 默认单包上限 1024 MB，单次网络读取超时 90 秒。超时不是整个任务的绝对时限，工作流总时限为 25 分钟；大量大包可调低频率或拆分源。
- 任意启用条目失败，本次不发布，已部署的 Pages 源保持原状；修复后手动重跑。没有发行版或找不到匹配 IPA 也视为失败，**不静默跳过**，避免上游异常使已有应用消失。只有你主动清空 `projects`，才会正常发布空源。
- 本地对可捕获的文件写入异常执行尽力回滚；不承诺断电/进程被杀或磁盘故障下的跨文件事务一致性。工作流必须成功完成全部生成步骤后才上传/部署 Pages，因此构建失败不会覆盖线上源。
- 本版本只提供每个 App 的**当前选定版本**，不维护历史版本列表和回滚仓库。
- GitHub API 使用工作流自动提供的 `GITHUB_TOKEN`，不要求填写个人令牌。API 凭据只发送至 GitHub API 主机，不转发给 IPA 下载 CDN。
- 如果怀疑缓存出错，可在 GitHub Actions 的 Caches 页面删掉 `ipa-meta-...` 缓存后重跑。首次无缓存也能完整生成。
- 公开仓库长期无活动时，GitHub 可能暂停定时工作流；请检查 Actions 状态，必要时手动恢复。Pages 首次部署、客户端缓存、GitHub 调度均可能导致看到旧数据。

`public/status.json` 公开列出本次选择的上游链接、版本、包大小与 SHA-256，便于核对；哈希只证明所下载文件的指纹，**不证明安全或来源可信**。

## 五、本地运行与测试

需要 Python 3.11+，没有 pip 依赖：

```sh
python3 -m unittest discover -s tests -v
python3 scripts/update.py
```

空配置不访问网络。收录应用时，设置 `config.json → source.base_url` 为实际 HTTPS 首页（不要带 `/source.json`），或：

```sh
SOURCE_BASE_URL=https://YOURNAME.github.io/ipa-source python3 scripts/update.py
```

本地预览可运行 `python3 -m http.server 8080 --directory public`，浏览器打开 `http://127.0.0.1:8080`；这只是预览，不是可订阅的 HTTPS 服务。

## 六、安全与隐私

- 配置文件与工作流属于**可信代码**。不要直接运行陌生人提交的修改，不要把私有网络地址放入配置。URL 校验只做 HTTPS 与明显本地地址拦截，不是完整的 DNS 重绑定 / SSRF 防护。
- 只添加你有权分发/引用的公开应用；不要填带账号、令牌或临时签名的私有下载链接。URL 会原样进入公开源与状态文件。
- 首次探测会由工作流服务器请求上游 IPA；最终下载时，上游会看到客户端的下载请求。自定义外部图标也会向对应主机发起请求。
- 本首页无分析统计、登录表单、第三方 JS 或字体。添加请求会跳转到 GitHub，需要所有者账号登录和人工确认；Issue 内容是公开的。GitHub 托管平台本身可能记录正常请求。
- 自动读取 plist 和 SHA-256 不是恶意代码检测。LiveContainer 内应用的隔离边界不同于正常安装，勿混用不可信 IPA 与敏感账号。

## 文件结构

```text
config.json                        唯一日常编辑入口（空项目列表）
projects.example.json              不生效的配置示例
scripts/update.py                  聚合与元数据读取器
scripts/add_request.py              校验并登记手机 Issue 请求
scripts/dispatch_publish.py           触发源重新发布
scripts/make_icon.py               自制 PNG 图标生成器
.github/workflows/publish.yml      定时检查与 Pages 发布
.github/workflows/add-ipa.yml      所有者请求验证、写配置、触发发布
public/                            待发布静态站点与源
  source.json                      客户端订阅这个文件
  status.json                      公开文件指纹与上游信息
  index.html / style.css / app.js   首页展示与订阅地址复制
  add.html / add.js                 手机粘贴项目地址表单
  apple-touch-icon-v2.png           iPhone 主屏幕与网页品牌图标
  icon.png                          通用源图标
tests/                              离线回归测试
```

模板代码使用 MIT 许可；上游 App 的许可与分发权独立，不受本模板许可覆盖。

## 七、在 LiveContainer 添加源

部署成功后，用 Safari 打开你的 HTTPS 首页，点“添加到 LiveContainer”；首页会生成官方格式的导入链接：

```text
livecontainer://source?url=<完整百分号编码后的 HTTPS source.json 地址>
```

也可手动打开 LiveContainer 的 **Sources（源）→ +**，粘贴完整 `source.json` 地址。入口名称会随语言和版本变化。空模板显示“没有可用应用”是正常的，不是更新错误；添加真实项目并成功部署后再刷新。

若点按钮无法唤起 App，优先复制地址手动添加，核对你所装版本是否支持 Sources。不要用本地 `minis://` 预览地址导入。

格式依据（静态核对日期：2026-11-22 UTC+8）：
- [LiveContainer 官方源指南](https://github.com/LiveContainer/LiveContainer.github.io/blob/main/docs/guides/sources.md)
- [核对过的 LiveContainer 解析器提交](https://github.com/LiveContainer/LiveContainer/blob/b0f53e8934df325970e9b6ff52932a43b585c424/LiveContainerSwiftUI/Views/LCAltStoreSourcesView.swift)
- [AltStore 官方源与权限规范](https://github.com/altstoreio/FAQ/blob/main/developers/make-a-source.md#app-permissions)
