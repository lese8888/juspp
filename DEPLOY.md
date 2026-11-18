# 免费采集与发布

## GitHub Actions 与 Pages

仓库是公开的，因此标准 GitHub-hosted runner 和 GitHub Pages 可免费使用。工作流每天运行一次（计划在香港时间 08:00），读取 `sources.json` 里的 RSS/Atom 源，筛选最近 24 小时内容，再用 Argos Translate 离线模型生成简体中文标题、摘要和视频口播稿，最后更新 `news.json` 和中文聚合订阅 `rss.xml`。

GitHub 的定时任务可能排队延迟；08:00 是计划时间，不是精确到点的服务保证。该免费方案运行短时采集与翻译任务，不运行常驻 Node 进程，也不需要翻译 API 密钥。新闻卡片默认展示中文，详情页保留来源原文以便核对；机器翻译稿发布前仍需人工确认专有名词、数字和传闻。

首次部署前，在仓库 **Settings → Pages → Build and deployment** 中将 **Source** 设为 **GitHub Actions**。此项完成后，推送工作流会首次采集并发布。站点地址为 `https://lese8888.github.io/juspp/`，聚合 RSS 地址为 `https://lese8888.github.io/juspp/rss.xml`。

以后每次有新闻数据变化，工作流会将 `news.json` 和 `rss.xml` 提交到 `main` 并重新发布。没有新数据时不会产生提交或重复发布。公开仓库的标准 GitHub Actions runner 使用量免费；私有仓库超出账户配额后可能产生费用。

## 本地常驻预览

安装 Node.js 与 Python 3.11 后，在项目目录先安装翻译工具，再运行预览：

```bash
python -m pip install argostranslate
npm start
```

首次翻译时会自动下载英语到中文模型。本地服务默认监听 `http://localhost:8765`，启动时采集并翻译一次，之后每 24 小时采集；网页可用 `/api/news.json` 取数据，`/rss.xml` 提供中文聚合订阅。可通过 `PORT` 和 `CRAWL_INTERVAL_MS` 设置端口和采集间隔。

## 新闻源

`sources.json` 中启用且带有 RSS/Atom 地址的来源会自动读取。任天堂官方页目前没有配置订阅地址，因此保留为人工核验来源。若所有订阅源均连接失败，采集器会保留上次已生成的数据。

