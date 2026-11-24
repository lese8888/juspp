# 部署与发布

## GitHub Pages

正式采集流程为 `.github/workflows/game-intel.yml`，每天香港时间 08:00（UTC `0 0 * * *`）运行，也支持手动触发。它安装 Argos Translate、OpenCC 和 Node 依赖，缓存离线翻译模型，执行语法与数据测试，采集最近 24 小时的 RSS，生成五种语言的数据，并把有变化的数据提交回 `main`。

静态发布由独立的 `.github/workflows/pages.yml` 完成。`index.html`、`news.json` 或 `rss.xml` 更新后，它会自动打包发布 GitHub Pages，避免采集任务与静态发布任务互相阻塞。

仓库公开时可以使用标准 GitHub-hosted runner 与 GitHub Pages。首次部署前，在仓库 **Settings → Pages → Build and deployment** 中将 **Source** 设为 **GitHub Actions**。站点地址为 `https://lese8888.github.io/juspp/`，简体中文 RSS 为 `https://lese8888.github.io/juspp/rss.xml`。

采集结果只有在全部新闻的五种语言标题和摘要都通过校验后才会替换 `news.json` 与 `rss.xml`。任一模型缺失、摘要为空或翻译结果残缺时，任务失败并保留上一份有效数据，不发布部分语言。

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

检查包含 Node/Python 语法、RSS 清洗与热度测试、翻译失败保护测试，以及当前 `news.json` / `rss.xml` 的五语完整性、字段、排序和订阅一致性验证。

页面接口：

- `/`：五语新闻网页
- `/news.json` 与 `/api/news.json`：`schemaVersion: 2` 公开新闻数据
- `/rss.xml`：继续使用 `translations["zh-CN"]` 的简体中文 RSS

## 边界

本轮只展示 RSS 提供的标题和摘要，不抓取文章完整正文，不增加历史归档或独立五语 RSS。机器翻译用于阅读辅助，专有名词、数字和传闻应以原文链接为准。
