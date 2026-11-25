# S4 真实全文影子状态

> 更新日期：2026-11-25（香港时间）
> 状态：**真实20篇与75篇影子均已完成；75/75通过自动闸门，等待人工五语质量签收**

## 一、已完成

- S1 冻结基线、逐篇哈希、写入门禁、调度审计通过；
- S2 全文翻译、长文分段、摘要、质量闸门和缓存模块通过；
- S3 混合影子链路通过；
- 真实 75 篇影子运行 `45549719200` 通过，计划与实际模式均为 `full_l10n`；
- `npm run check` 全量通过，共 147 次测试执行，去重后 140 项；
- 401、429、5xx、超时、空正文和无效 JSON 故障注入通过；
- 生产 `news.json`、`rss.xml` 未被修改；
- 远端“游闻多语言新闻采集”保持 `disabled_manually`。

## 二、已确认的外部条件

只读检查确认 GitHub 仓库已存在：

```text
Secret: LLM_API_KEY（存在，未读取值）
Variable: LLM_MODEL=deepseek-flash
```

## 三、已批准的运行边界

用户已批准：

```text
单次费用硬上限：¥20
程序与 Actions 硬截止：60分钟
```

预算门禁会在每次请求前预留最坏成本，失败请求也保留保守预留；任一上限
触线都会停止后续模型调用。

当前工作已提交到独立分支：

```text
分支：codex/full-l10n-shadow-s4
基础结果提交：5f775f0
```

生产 `main`、Pages 发布、邮件发送和定时采集仍然禁止。S5 生产适配代码可在
该分支继续开发和验收，但不能据此认定生产已经完成切流。

## 四、建议的批准参数

| 项目 | 建议值 |
|---|---:|
| 样本 | 与历史 20 篇相同的固定样本 |
| 输入 | `.agent-baselines/news-75-1fd6c3b33383.json` |
| 内容策略 | 自动路由；当前 75 篇预计全部 `full_l10n` |
| 目标语言 | `zh-CN`、`en`、`ja`、`ko`，`zh-TW` 由 OpenCC |
| 并发 | 3 |
| 单次硬截止 | 60 分钟 |
| 单次预算硬上限 | ¥20，已批准 |
| 输出 | 仅 `.shadow-output/` 和 Actions artifact |
| 邮件 / Pages / 生产数据 | 禁止 |

## 五、批准后执行顺序

1. 提交到独立 `codex/` 分支，不直接改 `main`；
2. 推送分支并手动触发影子工作流；
3. 检查 20/20 结构、五语、数字、日期、专名、正文覆盖和截断；
4. 下载 artifact 与离线审核页；
5. 人工完成五语内容和事实审核；
6. 只有审核通过后才进入 75 篇真实影子、生产 Dry Run 和人工灰度。

## 六、阻塞条件

以下条件仍必须保持满足：

- 单次预算硬上限已由用户确认为 ¥20；
- 当前工作只能在独立分支和影子 artifact 中运行；
- 运行时间硬截止和失败不覆盖生产策略已生效。

尚未完成的运维事项：曾经暴露的 API 密钥仍应在灰度前轮换。本次 ¥20
硬上限用于限制旧密钥在影子运行期间的风险，绝不进入生产发布。

## 七、当前结论

```text
engineering_ready: true
budget_approved_cny: 20
hard_deadline_minutes: 60
real_model_shadow: passed_20_of_20
real_model_final_run: 45549719200
real_model_cost_cny: 10.284478
real_model_duration_seconds: 2115.944
full_shadow_generated_articles: 75
full_shadow_generated_expected: 75
full_shadow_hard_failures: 0
full_shadow_warning_count: 244
full_shadow_budget_exceeded: false
full_shadow_deadline_exceeded: false
full_shadow_cumulative_cost_cny: 31.752156
manual_quality_review: pending
production_switch_ready: false
news_75_frozen: true
production_files_modified: false
s5_code_ready_in_branch: true
s5_published: false
```
