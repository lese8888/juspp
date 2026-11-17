# 云端部署

## 当前方案

- GitHub Actions 每天 00:00 UTC 运行，对应香港时间 08:00。
- 自动读取 `sources.json` 中启用且有 RSS 地址的来源，筛选过去 24 小时的文章并按链接去重。
- 生成 `news.json`，发送简报邮件，并将网页发布到 GitHub Pages。
- 手动运行工作流也会采集、发送并部署；代码推送只采集并部署，不发邮件。

GitHub Actions 的定时任务使用 UTC，且高负载时可能延迟。每天 08:00 是预期触发时间，不是精确送达保证。

## 首次部署步骤

1. 将 `game-intel` 目录和仓库根目录的 `.github/workflows/game-intel.yml` 推送到 GitHub 仓库默认分支。
2. 在仓库的 **Settings → Pages → Build and deployment** 中，将来源设为 **GitHub Actions**。
3. 在 **Settings → Secrets and variables → Actions** 添加仓库 Secrets：
   - `GMAIL_USER`：用于发信的 Gmail 地址。
   - `GMAIL_APP_PASSWORD`：该 Gmail 账户生成的应用专用密码。不要使用 Gmail 登录密码，也不要提交到代码。
   - `RECIPIENT_EMAIL`：简报收件地址。
4. 等待首次工作流发布；网页地址会显示在 Actions 的 `github-pages` 环境中。把该地址作为仓库变量 `SITE_URL` 添加后，邮件会包含网页链接。

## 来源说明

当前采集器读取 RSS/Atom。清单中没有订阅地址的官方站点暂时只作为人工核验来源，不会被自动抓取。文章时间缺失或无法解析时会被跳过。RSS 源的可用性可能变化，部署后应查看 Actions 日志。

