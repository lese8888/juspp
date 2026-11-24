# 游闻 LLM 影子验证

影子验证只生成候选数据和审核报告，不修改 `news.json`、`rss.xml`，不提交数据，也不触发 Pages 发布。

## 本地 mock 验证

```bash
npm run shadow:mock
python -m unittest discover -s tests -p "test_shadow_translation.py"
```

产物位于 `.shadow-output/`：

- `news.shadow.json`：候选五语数据，模拟 `schemaVersion: 3`；
- `translation-report.json`：请求数、token、成本、失败和警告。

mock 结果只验证程序链路，不代表模型翻译质量。

## 真实模型影子验证

在 GitHub 仓库中配置：

- Secret：`LLM_API_KEY`
- Variable：`LLM_BASE_URL`，默认 `https://api.deepseek.com/v1`
- Variable：`LLM_MODEL`，默认 `deepseek-chat`

然后手动运行 `translation-shadow.yml`，建议首次参数：

```text
engine=llm
limit=20
language=空（生成 zh-CN、en、ja、ko，zh-TW 由 OpenCC 生成）
```

任何单篇硬失败都会让工作流失败，但候选和报告仍会上传，供定位问题。

## 当前边界

- 内容模式：500 字摘要，`digest`；
- 不覆盖正式新闻数据；
- 不发送邮件；
- 不触发页面发布；
- 不修改 Cloudflare Worker；
- 不进入 Git 历史。
