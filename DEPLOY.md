# 云端部署

## 实时采集服务

实时采集使用常驻 Node.js 服务，不由 GitHub Pages 工作流执行。安装 Node.js 后，在本目录运行：

```bash
npm install
npm start
```

服务默认监听 `8765` 端口，启动时立即采集一次 RSS，之后每 5 分钟采集一次；网页每分钟检查新闻数据并自动更新。可通过环境变量 `PORT` 修改服务端口，通过 `CRAWL_INTERVAL_MS` 修改采集间隔（毫秒）。

要在公网持续实时更新，需要把此 Node 服务部署到支持常驻进程的主机，并让网页通过该服务提供的页面和 `/api/news.json` 访问。GitHub Pages 只能发布静态文件，现有 Pages 工作流仍是每日静态发布，不能代替常驻采集服务。

## Render 部署

仓库根目录的 `render.yaml` 定义了一个新加坡常驻 Web 服务、1 GB 持久化数据盘和 5 分钟 RSS 采集周期。将仓库连接到 Render 后，在 Dashboard 选择 **New → Blueprint** 并部署该 Blueprint。Render Starter 常驻实例当前从约 US$7/月起，1 GB 持久化磁盘另计；创建前请在 Render 账单预览中确认实际金额。

部署成功后，Render 会分配 `onrender.com` 地址。该地址同时提供网页和 `/api/news.json` 接口。此服务从源站读取 RSS/Atom，不需要另外部署 RSSHub；现有清单中没有 RSS 地址的任天堂官网暂时仍只作为人工核验来源。

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

