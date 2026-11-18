# 免费采集与发布

## GitHub Actions 与 Pages

仓库是公开的，因此标准 GitHub-hosted runner 和 GitHub Pages 可免费使用。工作流每 5 分钟读取 `sources.json` 里的 RSS/Atom 源，筛选最近 24 小时内容，生成 `news.json` 和独立聚合订阅 `rss.xml`，有新内容时自动发布网页。

GitHub 的定时任务可能排队延迟；5 分钟是配置间隔，不是精确到点的服务保证。该免费方案运行短时采集任务，不运行常驻 Node 进程。

首次部署前，在仓库 **Settings → Pages → Build and deployment** 中将 **Source** 设为 **GitHub Actions**。此项完成后，推送工作流会首次采集并发布。站点地址为 `https://lese8888.github.io/juspp/`，聚合 RSS 地址为 `https://lese8888.github.io/juspp/rss.xml`。

以后每次有新闻数据变化，工作流会将 `news.json` 和 `rss.xml` 提交到 `main` 并重新发布。没有新数据时不会产生提交或重复发布。公开仓库的标准 GitHub Actions runner 使用量免费；私有仓库超出账户配额后可能产生费用。

## 本地常驻预览

安装 Node.js 后，在项目目录运行：

```bash
npm start
```

本地服务默认监听 `http://localhost:8765`，启动时采集一次，之后每 5 分钟采集；网页可用 `/api/news.json` 取数据，`/rss.xml` 提供聚合 Feed。可通过 `PORT` 和 `CRAWL_INTERVAL_MS` 设置端口和采集间隔。

## 新闻源

`sources.json` 中启用且带有 RSS/Atom 地址的来源会自动读取。任天堂官方页目前没有配置订阅地址，因此保留为人工核验来源。若所有订阅源均连接失败，采集器会保留上次已生成的数据。

