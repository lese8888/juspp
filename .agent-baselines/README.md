# 新闻冻结基线

本目录保存游闻项目原始 75 条新闻的只读冻结副本，用于阻止翻译改造、
影子测试或采集任务意外修改已经发布的新闻。

## 硬限制

- 原 75 条新闻的全部字段不得改变，包括标题、摘要、正文、URL、来源、
  发布时间、时间戳、热度、分类和五语翻译。
- 原 75 条新闻的顺序不得改变，并且必须继续占据 `articles` 数组的前
  75 个位置。
- 原 75 条新闻不得删除、去重或替换。
- 新增文章只能显式追加到第 75 条之后；默认校验不会自动接受新增文章，
  必须使用 `--allow-appended-articles`。
- 任何领域哈希、文章块哈希或顺序变化都必须硬失败，不得自动修复。

## 文件

- `news-75-<hash>.json`：生产 `news.json` 的逐字节冻结副本。
- `manifest.json`：文章数量、文件哈希、文章块哈希、顺序键和逐篇哈希。

## 命令

```powershell
python scripts/check-news-baseline.py check
python scripts/check-news-baseline.py check --allow-appended-articles
python -m unittest tests.test_news_baseline
```

冻结副本只用于读取和比较。开发、影子、测试和 Dry Run 流程不得把结果
写入本目录或生产 `news.json`。
