#!/usr/bin/env node

import { mkdtemp, readFile, stat, writeFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { execFile } from "node:child_process";
import { tmpdir } from "node:os";
import { resolve } from "node:path";
import { promisify } from "node:util";
import { fileURLToPath } from "node:url";
import { pathToFileURL } from "node:url";

const projectRoot = resolve(fileURLToPath(new URL("..", import.meta.url)));
const defaultRunDir = resolve(
  projectRoot,
  ".shadow-output",
  "llm-45392103153",
);
const args = process.argv.slice(2);
const flags = new Set(args.filter((arg) => arg.startsWith("--")));
const positionalArgs = args.filter((arg) => !arg.startsWith("--"));
const runDir = resolve(positionalArgs[0] || defaultRunDir);
const newsPath = resolve(runDir, "news.shadow.json");
const reportPath = resolve(runDir, "translation-report.json");
const outputPath = resolve(runDir, "shadow-review.html");

function embedJson(value) {
  return JSON.stringify(value)
    .replaceAll("<", "\\u003c")
    .replaceAll("\u2028", "\\u2028")
    .replaceAll("\u2029", "\\u2029");
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

const news = JSON.parse(await readFile(newsPath, "utf8"));
let report = null;

try {
  report = JSON.parse(await readFile(reportPath, "utf8"));
} catch (error) {
  if (error.code !== "ENOENT") throw error;
}

const itemCount = Array.isArray(news.items)
  ? news.items.length
  : Array.isArray(news.articles)
    ? news.articles.length
    : 0;

const generatedAt =
  news.generatedAt || news.generated_at || news.generated || "未记录";
const fullCount = Number(
  report?.actualPolicyCounts?.full_l10n ??
  news.actualPolicyCounts?.full_l10n ??
  0,
);
const digestCount = Number(
  report?.actualPolicyCounts?.digest ??
  news.actualPolicyCounts?.digest ??
  0,
);
const contentModeLabel = digestCount
  ? `混合模式（完整译文 ${fullCount} / 内容摘要 ${digestCount}）`
  : `完整五语译文（${fullCount || itemCount} 篇）`;
const runId = /llm-\d+/.exec(runDir)?.[0] || "shadow-run";
const sampleItem = news.items?.[0] || news.articles?.[0] || {};
const sampleTranslation =
  sampleItem.translations?.["zh-CN"] ||
  sampleItem.i18n?.["zh-CN"] ||
  sampleItem.localizations?.["zh-CN"] ||
  sampleItem.content?.["zh-CN"] ||
  sampleItem["zh-CN"] ||
  {};
const sampleTranslationFields = Object.fromEntries(
  Object.entries(sampleTranslation).map(([key, value]) => [
    key,
    typeof value === "string" ? value.length : typeof value,
  ]),
);

const html = `<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="light dark">
  <title>游闻 · 五语影子审核（${escapeHtml(runId)}）</title>
  <style>
    :root {
      --paper: #f4f0e8;
      --paper-strong: #fffdf8;
      --ink: #171816;
      --muted: #6b6a63;
      --line: #d9d3c8;
      --accent: #b23a2b;
      --accent-soft: #f1d8d2;
      --ok: #27714d;
      --warn: #9b641a;
      --shadow: 0 12px 40px rgb(51 44 32 / 10%);
      --serif: "Songti SC", "Noto Serif CJK SC", SimSun, Georgia, serif;
      --sans: "Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", Arial, sans-serif;
    }
    * { box-sizing: border-box; }
    html { scroll-behavior: smooth; }
    body {
      margin: 0;
      min-height: 100vh;
      overflow-x: hidden;
      color: var(--ink);
      background:
        linear-gradient(90deg, rgb(130 118 96 / 5%) 1px, transparent 1px) 0 0 / 28px 28px,
        radial-gradient(circle at 12% 0%, #fff 0, transparent 34rem),
        var(--paper);
      font-family: var(--sans);
    }
    button, input, select { font: inherit; }
    button { cursor: pointer; }
    a { color: var(--accent); text-underline-offset: 3px; }
    .shell { width: min(1180px, calc(100% - 36px)); margin: 0 auto; }
    .masthead {
      position: relative;
      padding: 34px 0 24px;
      border-bottom: 1px solid var(--line);
    }
    .eyebrow {
      display: flex;
      align-items: center;
      gap: 10px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 800;
      letter-spacing: .16em;
      text-transform: uppercase;
    }
    .eyebrow::before {
      width: 34px;
      height: 8px;
      content: "";
      background: var(--accent);
    }
    h1 {
      max-width: 900px;
      margin: 13px 0 12px;
      font-family: var(--serif);
      font-size: clamp(38px, 7vw, 82px);
      font-weight: 900;
      letter-spacing: -.055em;
      line-height: .96;
    }
    .dek {
      max-width: 760px;
      margin: 0;
      color: #46453f;
      font-family: var(--serif);
      font-size: clamp(16px, 2vw, 20px);
      line-height: 1.65;
      overflow-wrap: anywhere;
    }
    .run-meta {
      display: flex;
      flex-wrap: wrap;
      gap: 8px 18px;
      margin-top: 18px;
      color: var(--muted);
      font-size: 13px;
    }
    .run-meta code {
      padding: 2px 6px;
      border: 1px solid var(--line);
      border-radius: 4px;
      background: rgb(255 255 255 / 55%);
      color: var(--ink);
      font-family: ui-monospace, Consolas, monospace;
    }
    .stats {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 12px;
      padding: 22px 0;
    }
    .stat {
      min-height: 116px;
      padding: 18px 20px;
      border: 1px solid var(--line);
      border-radius: 2px;
      background: rgb(255 253 248 / 74%);
    }
    .stat strong {
      display: block;
      margin-bottom: 6px;
      font-family: var(--serif);
      font-size: 35px;
      line-height: 1;
    }
    .stat span { color: var(--muted); font-size: 13px; }
    .notice {
      margin-bottom: 24px;
      padding: 15px 18px;
      border-left: 4px solid var(--warn);
      background: #fff7e8;
      color: #5d451e;
      font-size: 14px;
      line-height: 1.65;
      overflow-wrap: anywhere;
    }
    .toolbar {
      position: sticky;
      z-index: 20;
      top: 0;
      display: grid;
      grid-template-columns: minmax(220px, 1.6fr) minmax(180px, .7fr) minmax(180px, .7fr);
      gap: 12px;
      padding: 13px;
      border: 1px solid var(--line);
      background: rgb(244 240 232 / 94%);
      box-shadow: var(--shadow);
      backdrop-filter: blur(16px);
    }
    .toolbar > * { min-width: 0; }
    .field {
      display: grid;
      gap: 5px;
      min-width: 0;
    }
    .field > span {
      color: var(--muted);
      font-size: 11px;
      font-weight: 800;
      letter-spacing: .08em;
    }
    input, select {
      width: 100%;
      min-height: 42px;
      padding: 10px 12px;
      border: 1px solid var(--line);
      border-radius: 0;
      outline: none;
      background: var(--paper-strong);
      color: var(--ink);
    }
    input:focus, select:focus { border-color: var(--accent); box-shadow: 0 0 0 2px var(--accent-soft); }
    .language-bar {
      display: flex;
      gap: 6px;
      overflow-x: auto;
      padding: 18px 0 2px;
      scrollbar-width: thin;
      max-width: 100%;
    }
    .language-bar button {
      flex: 0 0 auto;
      padding: 9px 14px;
      border: 1px solid var(--line);
      border-radius: 999px;
      background: transparent;
      color: var(--muted);
      font-size: 13px;
      font-weight: 800;
    }
    .language-bar button[aria-pressed="true"] {
      border-color: var(--ink);
      background: var(--ink);
      color: #fff;
    }
    .result-line {
      display: flex;
      justify-content: space-between;
      gap: 20px;
      margin: 20px 0 10px;
      color: var(--muted);
      font-size: 13px;
    }
    .stories { display: grid; gap: 18px; padding-bottom: 54px; }
    .story {
      position: relative;
      padding: clamp(20px, 3vw, 34px);
      border: 1px solid var(--line);
      background: var(--paper-strong);
      box-shadow: 0 1px 0 rgb(0 0 0 / 3%);
    }
    .story::before {
      position: absolute;
      top: -1px;
      left: 32px;
      width: 72px;
      height: 4px;
      content: "";
      background: var(--accent);
    }
    .story-head {
      display: grid;
      grid-template-columns: 54px 1fr;
      gap: 16px;
      align-items: start;
    }
    .story-index {
      color: var(--accent);
      font-family: var(--serif);
      font-size: 29px;
      font-weight: 900;
      line-height: 1;
    }
    .story h2 {
      margin: 0;
      font-family: var(--serif);
      font-size: clamp(24px, 3.4vw, 39px);
      letter-spacing: -.025em;
      line-height: 1.15;
    }
    .story-meta {
      display: flex;
      flex-wrap: wrap;
      gap: 7px 14px;
      margin-top: 11px;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.5;
    }
    .story-meta .source { color: var(--ink); font-weight: 800; }
    .story-summary {
      margin: 22px 0 0 70px;
      font-family: var(--serif);
      font-size: clamp(16px, 2vw, 19px);
      line-height: 1.85;
      white-space: pre-wrap;
    }
    .story-summary p { margin: 0 0 1em; }
    .story-actions {
      display: flex;
      flex-wrap: wrap;
      gap: 9px;
      margin: 22px 0 0 70px;
    }
    .story-actions a, .story-actions summary {
      display: inline-flex;
      align-items: center;
      min-height: 36px;
      padding: 7px 11px;
      border: 1px solid var(--line);
      color: var(--ink);
      font-size: 12px;
      font-weight: 800;
      text-decoration: none;
      list-style: none;
    }
    .story-actions a:hover, .story-actions summary:hover { border-color: var(--accent); }
    .story-actions details { width: 100%; }
    .story-actions summary { width: max-content; cursor: pointer; }
    .extra {
      margin-top: 14px;
      padding-top: 2px;
      border-top: 1px dashed var(--line);
    }
    .extra-section { padding: 16px 0; border-bottom: 1px solid var(--line); }
    .extra-section:last-child { border-bottom: 0; }
    .extra-section h3 {
      margin: 0 0 8px;
      color: var(--accent);
      font-family: var(--sans);
      font-size: 12px;
      letter-spacing: .08em;
      text-transform: uppercase;
    }
    .extra-section p {
      margin: 0;
      font-family: var(--serif);
      font-size: 15px;
      line-height: 1.75;
      white-space: pre-wrap;
    }
    .empty {
      display: none;
      padding: 64px 24px;
      border: 1px dashed var(--line);
      text-align: center;
      color: var(--muted);
    }
    .footer {
      padding: 22px 0 34px;
      border-top: 1px solid var(--line);
      color: var(--muted);
      font-size: 12px;
      line-height: 1.7;
      overflow-wrap: anywhere;
    }
    .to-top {
      position: fixed;
      right: 20px;
      bottom: 20px;
      display: none;
      width: 44px;
      height: 44px;
      border: 0;
      border-radius: 50%;
      background: var(--ink);
      color: #fff;
      box-shadow: var(--shadow);
      font-size: 20px;
    }
    .to-top.visible { display: block; }
    @media (max-width: 760px) {
      .shell {
        width: calc(100% - 22px);
        max-width: 1180px;
      }
      .masthead { padding-top: 24px; }
      .stats { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .stat { min-height: 98px; padding: 15px; }
      .stat strong { font-size: 29px; }
      .toolbar { grid-template-columns: 1fr; }
      .result-line span { max-width: 100%; overflow-wrap: anywhere; }
      .story-head { grid-template-columns: 38px 1fr; gap: 10px; }
      .story-index { font-size: 22px; }
      .story-summary, .story-actions { margin-left: 0; }
      .result-line { align-items: flex-start; flex-direction: column; gap: 4px; }
    }
    @media print {
      :root { --paper: #fff; --paper-strong: #fff; }
      body { background: #fff; }
      .toolbar, .to-top { display: none !important; }
      .shell { width: 100%; }
      .story { break-inside: avoid; box-shadow: none; }
      .story-actions details { display: none; }
    }
  </style>
</head>
<body>
  <header class="masthead">
    <div class="shell">
      <div class="eyebrow">游闻 · Shadow Review</div>
      <h1>五语影子候选<br>离线审核页</h1>
      <p class="dek">收录本次影子运行的 ${itemCount} 篇候选。数据已嵌入本页，可离线搜索、切换语言并对照五种译文；所有内容仅供人工审核，尚未进入生产。</p>
      <div class="run-meta">
        <span>运行编号 <code>${escapeHtml(runId)}</code></span>
        <span>生成时间 ${escapeHtml(generatedAt)}</span>
        <span>内容模式 ${escapeHtml(contentModeLabel)}</span>
      </div>
    </div>
  </header>

  <main class="shell">
    <section class="stats" aria-label="运行统计">
      <div class="stat"><strong>${itemCount}</strong><span>候选文章</span></div>
      <div class="stat"><strong>5</strong><span>审核语言</span></div>
      <div class="stat"><strong id="source-count">—</strong><span>覆盖来源</span></div>
      <div class="stat"><strong id="warning-count">—</strong><span>质量警告</span></div>
    </section>

    <div class="notice">
      影子候选内容模式：${escapeHtml(contentModeLabel)}。候选仍只保存在离线目录，尚未进入生产。审核重点：事实、数字、日期、价格、平台、游戏名与厂商名，以及各语言是否自然。
    </div>

    <section class="toolbar" aria-label="筛选工具">
      <label class="field">
        <span>搜索标题、摘要与来源</span>
        <input id="search" type="search" placeholder="输入游戏名、厂商或关键词…" autocomplete="off">
      </label>
      <label class="field">
        <span>来源</span>
        <select id="source-filter"><option value="">全部来源</option></select>
      </label>
      <label class="field">
        <span>排序</span>
        <select id="sort-order">
          <option value="original">影子候选顺序</option>
          <option value="newest">发布时间：新到旧</option>
          <option value="oldest">发布时间：旧到新</option>
          <option value="source">按来源名称</option>
        </select>
      </label>
    </section>

    <nav class="language-bar" aria-label="审核语言">
      <button type="button" data-lang="zh-CN" aria-pressed="true">简体中文</button>
      <button type="button" data-lang="zh-TW" aria-pressed="false">繁體中文</button>
      <button type="button" data-lang="en" aria-pressed="false">English</button>
      <button type="button" data-lang="ja" aria-pressed="false">日本語</button>
      <button type="button" data-lang="ko" aria-pressed="false">한국어</button>
    </nav>

    <div class="result-line">
      <span id="result-count">显示 ${itemCount} 篇</span>
      <span>提示：每篇底部“查看五种语言”可逐篇对照</span>
    </div>

    <section id="stories" class="stories" aria-label="影子候选文章"></section>
    <div id="empty" class="empty">没有符合当前筛选条件的文章。</div>

    <footer class="footer">
      本页为单文件离线审核页，不含跟踪脚本、外部字体或网络资源。原始候选与质量报告保存在同一运行目录。
    </footer>
  </main>
  <button class="to-top" type="button" aria-label="返回顶部">↑</button>

  <script id="shadow-data" type="application/json">${embedJson(news)}</script>
  <script id="shadow-report" type="application/json">${embedJson(report)}</script>
  <script>
    (() => {
      "use strict";

      const news = JSON.parse(document.getElementById("shadow-data").textContent);
      const report = JSON.parse(document.getElementById("shadow-report").textContent || "null");
      const items = Array.isArray(news.items)
        ? news.items
        : Array.isArray(news.articles)
          ? news.articles
          : [];
      const languages = [
        ["zh-CN", "简体中文"],
        ["zh-TW", "繁體中文"],
        ["en", "English"],
        ["ja", "日本語"],
        ["ko", "한국어"],
      ];
      const languageNames = Object.fromEntries(languages);
      const stories = document.getElementById("stories");
      const empty = document.getElementById("empty");
      const search = document.getElementById("search");
      const sourceFilter = document.getElementById("source-filter");
      const sortOrder = document.getElementById("sort-order");
      const resultCount = document.getElementById("result-count");
      const requestedLanguage = new URLSearchParams(window.location.search).get("lang");
      let activeLanguage = languages.some(([lang]) => lang === requestedLanguage)
        ? requestedLanguage
        : "zh-CN";

      const el = (tag, className, text) => {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined && text !== null) node.textContent = String(text);
        return node;
      };

      const pickString = (...values) => {
        for (const value of values) {
          if (typeof value === "string" && value.trim()) return value.trim();
        }
        return "";
      };

      const translationFor = (item, lang) => {
        const direct =
          item.translations?.[lang] ||
          item.i18n?.[lang] ||
          item.localizations?.[lang] ||
          item.content?.[lang] ||
          item[lang];
        if (direct && typeof direct === "object") return direct;
        if (typeof direct === "string") return { content: direct };
        return {};
      };

      const sourceNameFor = (item) => pickString(
        item.sourceName,
        item.source_name,
        typeof item.source === "string" ? item.source : "",
        item.source?.name,
        item.feed?.name,
        item.feedTitle,
        "未知来源",
      );

      const dateFor = (item) => pickString(
        item.publishedAt,
        item.published_at,
        item.pubDate,
        item.date,
        item.timestamp,
      );

      const urlFor = (item) => pickString(
        item.originalUrl,
        item.original_url,
        item.url,
        item.link,
        item.sourceUrl,
        item.source_url,
        item.source?.url,
      );

      const normalized = items.map((item, index) => {
        const t = translationFor(item, "zh-CN");
        return {
          item,
          index,
          source: sourceNameFor(item),
          date: dateFor(item),
          url: urlFor(item),
          searchText: [
            sourceNameFor(item),
            ...languages.map(([lang]) => {
              const tx = translationFor(item, lang);
              return [
                tx.title,
                tx.headline,
                tx.summary,
                tx.excerpt,
                tx.description,
                tx.digest,
                tx.content,
                tx.body,
                tx.text,
              ].filter(Boolean).join(" ");
            }),
          ].join(" ").toLocaleLowerCase(document.documentElement.lang),
        };
      });

      const uniqueSources = [...new Set(normalized.map((entry) => entry.source))]
        .filter(Boolean)
        .sort((a, b) => a.localeCompare(b, "zh-CN"));

      uniqueSources.forEach((source) => {
        const option = document.createElement("option");
        option.value = source;
        option.textContent = source;
        sourceFilter.append(option);
      });

      const warningCount = (() => {
        const candidates = [
          report?.warnings?.length,
          report?.summary?.warnings,
          report?.stats?.warnings,
          report?.warningCount,
        ];
        return candidates.find((value) => Number.isFinite(Number(value))) ?? "—";
      })();

      document.getElementById("source-count").textContent = uniqueSources.length || "—";
      document.getElementById("warning-count").textContent = warningCount;

      const titleFor = (tx, item, lang = activeLanguage) => pickString(
        tx.title,
        tx.headline,
        item.titles?.[lang],
        item.title?.[lang],
        typeof item.title === "string" ? item.title : "",
        "（该语言缺少标题）",
      );

      const summaryFor = (tx) => pickString(
        tx.content,
        tx.body,
        tx.text,
        tx.digest,
        tx.summary,
        tx.excerpt,
        tx.description,
        "（该语言缺少内容）",
      );

      const appendTextParagraphs = (parent, value) => {
        const text = pickString(value);
        if (!text) return false;
        const blocks = text.split(/\\n{2,}/).filter(Boolean);
        for (const block of blocks) parent.append(el("p", "", block));
        return true;
      };

      const buildExtraLanguages = (entry) => {
        const details = el("details");
        const summary = el("summary", "", "查看五种语言");
        details.append(summary);
        const wrap = el("div", "extra");
        for (const [lang, label] of languages) {
          const tx = translationFor(entry.item, lang);
          const section = el("section", "extra-section");
          section.append(el("h3", "", label + " · " + titleFor(tx, entry.item, lang)));
          appendTextParagraphs(section, summaryFor(tx));
          wrap.append(section);
        }
        details.append(wrap);
        return details;
      };

      const render = () => {
        const query = search.value.trim().toLocaleLowerCase("zh-CN");
        const source = sourceFilter.value;
        let filtered = normalized.filter((entry) => {
          if (source && entry.source !== source) return false;
          if (query && !entry.searchText.includes(query)) return false;
          return true;
        });

        if (sortOrder.value === "newest" || sortOrder.value === "oldest") {
          const direction = sortOrder.value === "newest" ? -1 : 1;
          filtered = [...filtered].sort((a, b) => {
            const at = Date.parse(a.date) || 0;
            const bt = Date.parse(b.date) || 0;
            return (at - bt) * direction || a.index - b.index;
          });
        } else if (sortOrder.value === "source") {
          filtered = [...filtered].sort((a, b) =>
            a.source.localeCompare(b.source, "zh-CN") || a.index - b.index
          );
        }

        stories.replaceChildren();
        filtered.forEach((entry, displayIndex) => {
          const tx = translationFor(entry.item, activeLanguage);
          const article = el("article", "story");
          const head = el("div", "story-head");
          head.append(el("div", "story-index", String(displayIndex + 1).padStart(2, "0")));
          const heading = el("div");
          const h2 = el("h2", "", titleFor(tx, entry.item));
          const meta = el("div", "story-meta");
          meta.append(el("span", "source", entry.source));
          if (entry.date) {
            const time = el("time", "", entry.date);
            time.dateTime = entry.date;
            meta.append(time);
          }
          meta.append(el("span", "", languageNames[activeLanguage]));
          heading.append(h2, meta);
          head.append(heading);
          article.append(head);

          const summary = el("div", "story-summary");
          appendTextParagraphs(summary, summaryFor(tx));
          article.append(summary);

          const actions = el("div", "story-actions");
          if (entry.url) {
            const original = el("a", "", "打开原文 ↗");
            original.href = entry.url;
            original.target = "_blank";
            original.rel = "noopener noreferrer";
            actions.append(original);
          }
          actions.append(buildExtraLanguages(entry));
          article.append(actions);
          stories.append(article);
        });

        resultCount.textContent = "显示 " + filtered.length + " / " + items.length + " 篇";
        empty.style.display = filtered.length ? "none" : "block";
      };

      document.querySelectorAll(".language-bar button").forEach((button) => {
        button.setAttribute("aria-pressed", String(button.dataset.lang === activeLanguage));
        button.addEventListener("click", () => {
          activeLanguage = button.dataset.lang;
          document.querySelectorAll(".language-bar button").forEach((candidate) => {
            candidate.setAttribute("aria-pressed", String(candidate === button));
          });
          render();
          window.scrollTo({ top: document.querySelector(".toolbar").offsetTop, behavior: "smooth" });
        });
      });

      search.addEventListener("input", render);
      sourceFilter.addEventListener("change", render);
      sortOrder.addEventListener("change", render);

      const toTop = document.querySelector(".to-top");
      window.addEventListener("scroll", () => {
        toTop.classList.toggle("visible", window.scrollY > 700);
      }, { passive: true });
      toTop.addEventListener("click", () => window.scrollTo({ top: 0, behavior: "smooth" }));

      render();
    })();
  </script>
</body>
</html>
`;

await writeFile(outputPath, html, "utf8");

const outputStats = await stat(outputPath);
const qa = {
  browser: null,
  languageChecks: {},
  screenshot: null,
  mobileScreenshot: null,
};

if (flags.has("--qa")) {
  const browserCandidates = [
    "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
    "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
    "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
    "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
  ];
  const browser = browserCandidates.find((candidate) => existsSync(candidate));

  if (!browser) {
    throw new Error("未找到可用于离线页面验证的 Edge 或 Chrome。");
  }

  qa.browser = browser;
  const profileDir = await mkdtemp(resolve(tmpdir(), "youwen-shadow-review-"));
  const pageUrl = pathToFileURL(outputPath).href;
  const commonArgs = [
    "--headless=new",
    "--disable-gpu",
    "--hide-scrollbars",
    "--disable-extensions",
    "--no-first-run",
    "--no-default-browser-check",
    `--user-data-dir=${profileDir}`,
  ];

  for (const lang of ["zh-CN", "zh-TW", "en", "ja", "ko"]) {
    const dump = await promisify(execFile)(
      browser,
      [
        ...commonArgs,
        "--virtual-time-budget=1200",
        "--dump-dom",
        `${pageUrl}?lang=${encodeURIComponent(lang)}`,
      ],
      { encoding: "utf8", timeout: 30000, maxBuffer: 20 * 1024 * 1024 },
    );
    const visibleMarkup = dump.stdout
      .replace(/<script[\s\S]*?<\/script>/gi, "")
      .replace(/<style[\s\S]*?<\/style>/gi, "");
    qa.languageChecks[lang] = {
      renderedStoryCount: (dump.stdout.match(/<article class="story">/g) || []).length,
      missingContent: visibleMarkup.includes("（该语言缺少内容）"),
      missingTitle: visibleMarkup.includes("（该语言缺少标题）"),
    };
  }

  const screenshotPath = resolve(runDir, "shadow-review-preview.png");
  await promisify(execFile)(
    browser,
    [
      ...commonArgs,
      "--window-size=1440,1200",
      "--force-device-scale-factor=1",
      "--virtual-time-budget=1200",
      `--screenshot=${screenshotPath}`,
      pageUrl,
    ],
    { encoding: "utf8", timeout: 30000, maxBuffer: 20 * 1024 * 1024 },
  );
  qa.screenshot = screenshotPath;

  const mobileScreenshotPath = resolve(runDir, "shadow-review-mobile.png");
  await promisify(execFile)(
    browser,
    [
      ...commonArgs,
      // Chromium 在 Windows 无界面模式下会把外框宽度钳制到约 500px。
      // 使用 500px 仍会触发移动端断点，也能避免截图被二次裁切。
      "--window-size=500,900",
      "--force-device-scale-factor=1",
      "--virtual-time-budget=1200",
      `--screenshot=${mobileScreenshotPath}`,
      pageUrl,
    ],
    { encoding: "utf8", timeout: 30000, maxBuffer: 20 * 1024 * 1024 },
  );
  qa.mobileScreenshot = mobileScreenshotPath;
}

console.log(
  JSON.stringify(
    {
      output: outputPath,
      outputBytes: outputStats.size,
      itemCount,
      generatedAt,
      reportIncluded: Boolean(report),
      sampleTranslationFields,
      qa,
    },
    null,
    2,
  ),
);
