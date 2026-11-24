# 部署与发布

## GitHub Pages

正式采集流程为 `.github/workflows/game-intel.yml`，每天香港时间 08:00（UTC `0 0 * * *`）运行，也支持手动触发。它安装 Scrapling、Argos Translate、OpenCC 和 Node 依赖，缓存离线翻译模型，从各来源的公开列表页或 sitemap 采集最近 24 小时新闻，并在采集后验证最新原文距离当前不超过 26 小时，生成五种语言的数据后把有变化的内容提交回 `main`。手动触发不会发送邮件，只有每天定时任务发送一次汇总。

静态发布由独立的 `.github/workflows/pages.yml` 完成。`index.html`、`news.json` 或 `rss.xml` 更新后，它会自动打包发布 GitHub Pages，避免采集任务与静态发布任务互相阻塞。

仓库公开时可以使用标准 GitHub-hosted runner 与 GitHub Pages。首次部署前，在仓库 **Settings → Pages → Build and deployment** 中将 **Source** 设为 **GitHub Actions**。站点地址为 `https://lese8888.github.io/juspp/`，简体中文 RSS 为 `https://lese8888.github.io/juspp/rss.xml`。

采集结果只有在全部新闻的五种语言标题和摘要都通过校验后才会替换 `news.json` 与 `rss.xml`。任一模型缺失、摘要为空或翻译结果残缺时，任务失败并保留上一份有效数据，不发布部分语言。

Actions 摘要会列出新闻总数、过去 24 小时数量、香港当天发布数量、最新原文时间、来源成功数和失败来源，便于确认每日任务是否真正更新。

## Cloudflare Workers 免费发布

Cloudflare 使用一个免费 Worker 反向代理 GitHub Pages，提供 `/`、`/news.json`、`/api/news.json` 和 `/rss.xml`。Worker 在 Cloudflare 边缘缓存内容 5 分钟，因此 GitHub Actions 每天更新 Pages 后，Cloudflare 会自动读取新版本，不需要把 Cloudflare 凭据放进 GitHub，也不需要每次新闻更新都重新部署 Worker。

该方案只使用 Workers Free 计划，不需要 Containers、D1、R2 或常驻服务器。发布地址：

```text
https://youwen-game-news.game-intel-brief.workers.dev/
```

首次创建或需要更新 Worker 代码时，在本机执行：

```powershell
npx wrangler login
npx wrangler deploy
```

日常采集与发布仍由 GitHub Actions 和 GitHub Pages 完成。Cloudflare 代理对访客保持同一公开接口，并在上游暂时不可用时返回明确错误；上一份 GitHub Pages 有效数据继续作为回退。

## Scrapling 正式采集

Scrapling 只在 GitHub-hosted runner 中运行，不部署到 Cloudflare Containers，因此保持免费。正式工作流读取公开列表页或 sitemap，使用新闻页 JSON-LD、Open Graph、`<time>` 和 sitemap 新闻发布日期生成原始报告，再进入五语翻译与发布链路。

采集报告会统计每个来源发现与保留的新闻数量、香港当天数量、与当前已发布数据的链接重叠数，以及抓取失败或无法确认发布时间的来源。无法确认原文发布时间的文章会被直接丢弃。

无法从 JSON-LD、页面元数据或 sitemap 新闻日期确认原文发布时间的文章会被丢弃，不能用抓取时间冒充原文发布时间。

## 本地预览

建议使用 Python 3.12：

```powershell
cd game-intel
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
npm start
```

本地服务默认监听 `http://localhost:8765`，启动时采集并生成五语数据，之后按 `CRAWL_INTERVAL_MS` 周期采集。首次运行会下载英语到简体中文、日语和韩语的 Argos 离线模型；繁体中文由 OpenCC `s2twp` 从简体中文转换。

## 检查

```powershell
cd game-intel
npm run check
```

检查包含 Node/Python 语法、Scrapling 元数据解析、新鲜度和热度测试、翻译失败保护测试，以及当前 `news.json` / `rss.xml` 的五语完整性、字段、排序和订阅一致性验证。

页面接口：

- `/`：五语新闻网页
- `/news.json` 与 `/api/news.json`：`schemaVersion: 2` 公开新闻数据
- `/rss.xml`：继续使用 `translations["zh-CN"]` 的简体中文 RSS

## 边界

本轮只展示来源页面提供的公开标题和摘要，不抓取文章完整正文，不增加历史归档或独立五语 RSS。机器翻译用于阅读辅助，专有名词、数字和传闻应以原文链接为准。
