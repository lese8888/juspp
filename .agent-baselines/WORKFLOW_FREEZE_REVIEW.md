# 工作流冻结风险审查

> 审查日期：2026-11-24（香港时间）
> 审查范围：`.github/workflows/`
> 结论：**仓库代码已能校验原 75 条，但当前生产工作流尚未调用该守卫，定时采集仍可绕过并覆盖 `news.json`。**

## 一、风险清单

| 文件 | 行号 | 命令或触发器 | 风险级别 | 对生产新闻的影响 |
|---|---:|---|---|---|
| `game-intel.yml` | 4-5 | `schedule` / `cron: '0 0 * * *'` | HIGH | 每日香港时间 08:00 自动启动采集，可能在无人确认时改写 `news.json` |
| `game-intel.yml` | 6 | `workflow_dispatch` | MEDIUM | 手动触发同样运行生产采集与写入链路 |
| `game-intel.yml` | 48 | `SEND_EMAIL` 根据 `schedule` 决定发送 | MEDIUM | 定时任务可能在数据已更新后发送汇总邮件 |
| `game-intel.yml` | 54 | `npm run collect` | HIGH | 采集和翻译可能直接生成新的 `news.json`、`rss.xml` |
| `game-intel.yml` | 77 | `git add news.json rss.xml` | HIGH | 将生产新闻和 RSS 加入待提交内容 |
| `game-intel.yml` | 83 | `git commit -m '更新游闻五语新闻数据'` | HIGH | 将生产数据写入 Git 历史 |
| `game-intel.yml` | 86 | `git push origin HEAD:main` | HIGH | 把覆盖后的生产数据推送到主分支 |
| `game-intel.yml` | 107 | `gh workflow run pages.yml --ref main` | HIGH | 用已提交数据触发 GitHub Pages 发布 |
| `pages.yml` | 11 | `workflow_dispatch` | MEDIUM | 可绕过采集流程单独发布当前工作树中的数据 |
| `pages.yml` | 34 | `cp index.html news.json rss.xml _site/` | HIGH | 把当前 `news.json` 和 `rss.xml` 复制进 Pages 制品 |
| `pages.yml` | 36 | `actions/upload-pages-artifact@v4` | HIGH | 固化准备发布的新闻制品 |
| `pages.yml` | 43 | `actions/deploy-pages@v4` | HIGH | 将制品发布到线上 Pages |
| `translation-shadow.yml` | 4 | `workflow_dispatch` | MEDIUM | 影子任务当前只上传 artifact，但入口仍需持续确认不写生产文件 |
| `maintenance.yml` | 4-5 | `schedule` / `cron: '0 1 * * *'` | MEDIUM | 定时维护可能检查并后续改动工作流或生产文件 |
| `maintenance.yml` | 6 | `workflow_dispatch` | MEDIUM | 手动维护可能绕过固定节奏 |

## 二、关键判定

1. `game-intel.yml` 的定时采集、提交、推送和 Pages 发布链路没有调用
   `scripts/check-news-baseline.py` 或 `translation/protected_articles.py`。
2. 因此，现有冻结清单只能作为事后发现工具；它不能阻止远端生产工作流
   直接覆盖 `news.json`。
3. `pages.yml` 会直接发布当前文件，发布入口也必须使用同一冻结校验，
   否则有人手动运行发布仍可能把错误数据上线。
4. 仅停止本地命令不能改变 GitHub 远端定时计划；要保证线上 75 条不动，
   必须停用远端 `game-intel.yml` 的 schedule，或接入提交前硬门禁后再允许
   定时任务运行。

## 三、安全方案

### 方案 A：冻结期暂停定时采集（当前推荐）

- 暂时移除或停用 `game-intel.yml` 的 `schedule`；
- 保留 `workflow_dispatch`，所有采集只能人工触发；
- 在采集、提交和 Pages 发布前增加冻结校验；
- 冻结期结束后，再通过人工审批恢复定时采集。

这是当前风险最低的方案，因为它先切断“每天 08:00 自动覆盖”这条外部路径。

### 方案 B：强制生产写入门禁

- 在 `npm run collect` 之后、`git add` 之前运行冻结校验；
- 生产写入只允许原始 75 条字段和顺序完全不变，并允许新文章追加到末尾；
- `pages.yml` 发布前再次执行同一校验；
- 校验失败时停止提交、停止 Pages、停止邮件并上传失败报告；
- 每次运行报告必须包含 `baselineHash`、`currentHash` 和变更文章列表。

方案 B 适合门禁接入完成后的长期运行，但不应当只靠“先运行、后校验”；
必须在提交和发布两个入口都硬失败。

## 四、验收清单

- [ ] 远端 `game-intel.yml` 的 schedule 已停止，或门禁已接入；
- [ ] 采集后、提交前执行冻结校验；
- [ ] Pages 发布前执行冻结校验；
- [ ] 原 75 条字段、顺序和逐篇哈希不变；
- [ ] 新文章只能追加在冻结文章之后；
- [ ] 校验失败不提交、不推送、不发布、不发邮件；
- [ ] Actions Summary 输出冻结状态和失败文章键。

## 五、已执行的冻结期缓解

- 2026-11-24 21:46（香港时间）已通过 GitHub CLI 停用远端
  `游闻多语言新闻采集`（workflow id `421204968`），当前状态为
  `disabled_manually`；
- 本地 `.github/workflows/game-intel.yml` 已移除 `schedule`，只保留
  `workflow_dispatch`；
- 该操作可逆，但恢复前必须先完成方案 B 的提交前与 Pages 发布前门禁；
- 当前 `news.json` 仍为 75 条，文件哈希与冻结基线一致。

上方风险行号和风险表记录的是缓解前的审查结果，用于说明为什么必须
暂停调度；本地工作流移除 schedule 后，审计脚本应返回 `passed`。
