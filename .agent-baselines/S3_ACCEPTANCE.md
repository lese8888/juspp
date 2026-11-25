# S3 独立验收报告

> 验收代理：4 号
> 验收日期：2026-11-24（香港时间）
> 验收范围：S1 新闻冻结、S2 生成模块、S3 混合影子链路
> 结论：**通过；真实模型质量、成本和耗时仍待 S4 验证**

## 一、执行命令

```powershell
npm run check
npm run baseline:check
npm run workflow:audit
python shadow_translate.py --engine mock --content-mode mixed --limit 75 `
  --input .agent-baselines/news-75-1fd6c3b33383.json `
  --output-dir .shadow-output/s4-acceptance-mock
```

## 二、全量测试

`npm run check` 全部通过：

| 测试层 | 执行数 | 结果 |
|---|---:|---|
| Node / Worker / RSS / 前端数据 | 19 | 通过 |
| 生产翻译 | 12 | 通过 |
| 旧影子翻译 | 7 | 通过 |
| S3 混合影子与旧影子合并命令 | 14 | 通过 |
| LLM 接口故障注入 | 5 | 通过 |
| 多语言路由 | 9 | 通过 |
| S2 全文、分段、摘要、闸门、缓存 | 50 | 通过 |
| S1 冻结、保护器、调度审计 | 31 | 通过 |

`npm run check` 共执行 147 次测试。旧影子 7 项在独立命令和合并命令中各执行
一次，因此去重后的测试用例数为 140。

语法检查、翻译占位符检查、Python 编译检查均通过。

## 三、冻结基线

```text
status: unchanged_exact
protectedArticleCount: 75
currentArticleCount: 75
appendedArticleCount: 0
issues: []
```

关键哈希：

```text
news.json SHA256
1fd6c3b33383990e093bafe77d954eb41a6ec96d7bb154abf500cf83f3110f92

规范化 articles SHA256
a62ecd0065562b2d6c95360c28266464b531c69355848f6ccb921a3e76918468

rss.xml SHA256
cf453532fb8d3e78ed686b897b969d53db238d59abcd8ffe37dde2e31c0b8286
```

验收前后生产文件哈希一致，生产文件未被修改。

## 四、调度审计

```text
status: passed
workflowCount: 4
scheduledProductionBypassFiles: []
```

`game-intel.yml` 已移除 `schedule`，仅保留 `workflow_dispatch`。远端
`游闻多语言新闻采集` 在此前已停用，当前不存在由定时工作流直接写入生产
文件的路径。

## 五、75 篇混合影子 Mock 验收

输入使用只读冻结副本：

```text
.agent-baselines/news-75-1fd6c3b33383.json
```

结果：

| 指标 | 实测值 |
|---|---:|
| 选中文章 | 75 |
| 生成文章 | 75 |
| 计划 `full_l10n` | 75 |
| 计划 `digest` | 0 |
| 实际 `full_l10n` | 75 |
| 实际 `digest` | 0 |
| 路由不一致 | 0 |
| 失败 | 0 |
| 警告 | 0 |
| 输入文件变化 | 否 |
| 生产文件修改 | 否 |

结构检查：

```text
articles: 75
allFiveLanguages: true
allFieldsNonEmpty: true
policies: ["full_l10n"]
```

五语顺序均为：

```text
zh-CN, zh-TW, en, ja, ko
```

## 六、产物

```text
.shadow-output/s4-acceptance-mock/news.shadow.json
.shadow-output/s4-acceptance-mock/translation-report.json
```

产物只存在于影子输出目录，不提交、不发布、不进入生产 `news.json` 或
`rss.xml`。

## 七、未解决风险

- 本次只运行 mock，不能证明真实模型的翻译质量、五语自然度和事实一致性；
- 不能据此确认完整正文模式的真实 token、费用、请求数和耗时；
- 75 篇当前全部为 `full_l10n`，真实全量运行时仍需预算上限和硬截止；
- 真实模型实验应通过人工手动触发，并只上传 artifact；
- 生产定时采集必须保持停用，直到冻结写入门禁接入采集提交和 Pages 发布路径。

## 八、验收结论

```yaml
agent: 4
step: S3
core_result: passed
production_files_modified: false
production_hashes_unchanged: true
shadow_mock_75: passed
real_model_validation: pending_s4
result: passed
```
