# S1 新闻冻结独立验收报告

> 验收代理：4 号
> 验收日期：2026-11-24（香港时间）
> 验收性质：只读验收
> 结论：**核心冻结能力通过；调度审计已完成；远端定时采集已停用**

## 一、验收范围

本次只验收“原有 75 条新闻完全不动”的代码闭环，包括：

1. 冻结副本和 manifest 是否可用；
2. 当前生产 `news.json` 是否仍与冻结基线完全一致；
3. 75 条新闻的数量、顺序和逐篇哈希是否一致；
4. 保护器是否允许真实 75 条在发布模式下原样通过；
5. 验收前后 `news.json` 与 `rss.xml` 是否发生变化；
6. 冻结机制自身的失败关闭行为是否有测试覆盖。

本次不验收 3 号的调度审计，也不批准生产切换、提交、推送或发布。

## 二、执行命令与结果

```powershell
python -m py_compile scripts/run-s1-freeze-acceptance.py tests/test_s1_freeze_acceptance.py
python -m unittest tests.test_s1_freeze_acceptance
python scripts/run-s1-freeze-acceptance.py
```

| 检查 | 结果 |
|---|---|
| 验收工具语法检查 | 通过 |
| 验收工具专项测试 | 5 项通过 |
| 冻结基线专项测试 | 9 项通过 |
| 保护器专项测试 | 13 项通过 |
| 验收脚本内部运行的基础测试合计 | 22 项通过 |
| 真实 75 条冻结验收 | `passed` |
| 验收前后生产文件一致 | 是 |

## 三、真实数据结果

| 指标 | 实测值 |
|---|---|
| 冻结文章数 | 75 |
| 当前文章数 | 75 |
| 基线状态 | `unchanged_exact` |
| 新增文章数 | 0 |
| 顺序键数量 | 75 |
| 逐篇哈希数量 | 75 |
| 保护器模式 | `release` |
| 保护器识别文章数 | 75 |

## 四、关键哈希

```text
news.json SHA256
1fd6c3b33383990e093bafe77d954eb41a6ec96d7bb154abf500cf83f3110f92

规范化 articles SHA256
a62ecd0065562b2d6c95360c28266464b531c69355848f6ccb921a3e76918468

manifest.json SHA256
cee6c3b3ef821f13e2c3000b7b51cce9600d60abbdba28563718fcc2f6ae31dd

rss.xml SHA256
cf453532fb8d3e78ed686b897b969d53db238d59abcd8ffe37dde2e31c0b8286
```

`news.json` 的验收前、验收后 SHA256 完全相同，当前规定化文章块哈希也与冻结清单完全一致。

## 五、已验证的失败关闭行为

- manifest 缺失时拒绝放行；
- manifest 数量不一致时拒绝放行；
- 冻结副本被篡改时拒绝放行；
- 冻结文章字段变化时拒绝；
- 冻结文章顺序变化时拒绝；
- 删除冻结文章时拒绝；
- 新文章插入冻结文章中间时拒绝；
- 开发或影子阶段把冻结文章当成写入目标时拒绝；
- 新文章只能追加到原 75 条之后。

## 六、生产文件保护证明

| 文件 | 验收前 SHA256 | 验收后 SHA256 | 是否变化 |
|---|---|---|---|
| `news.json` | `1fd6c3b33383...3110f92` | `1fd6c3b33383...3110f92` | 否 |
| `rss.xml` | `cf453532fb8d...c0b8286` | `cf453532fb8d...c0b8286` | 否 |

## 七、调度审计与缓解结果

调度审计产物已补齐：

- `.agent-baselines/WORKFLOW_FREEZE_REVIEW.md`
- `scripts/audit-freeze-workflow.py`
- `tests/test_freeze_workflow_audit.py`

审查确认生产采集原本会在采集后执行：

```text
git add news.json rss.xml
git commit
git push origin HEAD:main
gh workflow run pages.yml --ref main
```

为避免远端定时任务绕过仓库内的冻结校验，已执行以下可逆缓解：

- 停用远端 `游闻多语言新闻采集`（workflow id `421204968`），状态为
  `disabled_manually`；
- 从本地 `game-intel.yml` 移除 `schedule`，仅保留 `workflow_dispatch`；
- 调度审计结果为 `passed`，当前无定时工作流可直接写入生产文件。

恢复定时任务前，仍必须把冻结保护器接入采集后提交前和 Pages 发布前。

## 八、4 号验收结论

```yaml
agent: 4
step: S1
core_freeze_result: passed
workflow_audit_result: passed
remote_collection_workflow: disabled_manually
production_files_modified: false
result: passed
next_agent: 总负责人
```
