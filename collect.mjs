import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

import {
  escapeAttr,
  escapeHtml,
  sortArticlesByHeat
} from './lib/news.mjs';
import { assertFreshArticles, summarizeArticles } from './lib/freshness.mjs';

const root = path.dirname(fileURLToPath(import.meta.url));
const config = JSON.parse(await fs.readFile(path.join(root, 'sources.json'), 'utf8'));
const newsPath = process.env.NEWS_FILE
  ? path.resolve(process.env.NEWS_FILE)
  : path.join(root, 'news.json');
const rssPath = process.env.RSS_FILE
  ? path.resolve(process.env.RSS_FILE)
  : path.join(root, 'rss.xml');
const siteUrl = process.env.SITE_URL || 'https://youwen-game-news.game-intel-brief.workers.dev/';

function normalizeSourceResults(report) {
  return (report.sourceResults || []).map(source => ({
    name: source.name || '未知来源',
    ok: Boolean(source.ok),
    articleCount: Number(source.retained) || 0,
    discoveredCount: Number(source.discovered) || 0,
    latestPublishedAt: source.latestPublishedAt || null,
    error: Array.isArray(source.errors) ? source.errors.filter(Boolean).join('；') : source.error || ''
  }));
}

async function readScraplingReport() {
  if (process.env.SCRAPLING_INPUT_FILE) {
    return JSON.parse(await fs.readFile(path.resolve(process.env.SCRAPLING_INPUT_FILE), 'utf8'));
  }

  const stageDirectory = await fs.mkdtemp(path.join(os.tmpdir(), 'game-intel-scrapling-'));
  const rawPath = path.join(stageDirectory, 'scrapling-raw.json');
  const python = process.env.PYTHON_EXECUTABLE || (process.platform === 'win32' ? 'python' : 'python3');
  const result = spawnSync(python, [path.join(root, 'scrapling_collector.py')], {
    cwd: root,
    env: {
      ...process.env,
      SCRAPLING_OUTPUT_FILE: rawPath,
      COMPARE_NEWS_FILE: newsPath
    },
    encoding: 'utf8',
    maxBuffer: 20 * 1024 * 1024
  });
  if (result.stdout) process.stdout.write(result.stdout);
  if (result.stderr) process.stderr.write(result.stderr);

  try {
    const report = JSON.parse(await fs.readFile(rawPath, 'utf8'));
    if (result.error || result.status !== 0) {
      console.error(
        `Scrapling 采集过程未完全成功：${result.error?.message || `退出码 ${result.status}`}`
      );
    }
    return report;
  } catch (error) {
    throw new Error(
      `Scrapling 未生成有效原始报告：${result.error?.message || result.status || error.message}`
    );
  } finally {
    await fs.rm(stageDirectory, { recursive: true, force: true }).catch(() => {});
  }
}

function signatureOf(report) {
  return JSON.stringify((report?.articles || []).map(item => ({
    originalTitle: item.originalTitle,
    originalSummary: item.originalSummary,
    originalContent: item.originalContent,
    url: item.url,
    timestamp: item.timestamp,
    source: item.source,
    sourceTypeKey: item.sourceTypeKey,
    categoryKey: item.categoryKey
  })));
}

async function readPreviousReport() {
  try {
    return JSON.parse(await fs.readFile(newsPath, 'utf8'));
  } catch {
    return null;
  }
}

async function writeCollectSummary(summary) {
  const summaryPath = process.env.COLLECT_SUMMARY_FILE;
  if (!summaryPath) return;
  await fs.mkdir(path.dirname(path.resolve(summaryPath)), { recursive: true });
  await fs.writeFile(summaryPath, `${JSON.stringify(summary, null, 2)}\n`, 'utf8');
}

function translationRecord(article, language) {
  if (article.translations?.[language]) return article.translations[language];
  if (language === 'en') {
    return { title: article.originalTitle || article.title, summary: article.originalSummary || article.summary || '' };
  }
  return {
    title: article.title || article.originalTitle || '',
    summary: article.summary || article.originalSummary || ''
  };
}

async function replacePublishedFiles(files) {
  const token = `${process.pid}-${Date.now()}`;
  const backups = files.map(file => ({
    ...file,
    backup: `${file.target}.backup-${token}`,
    published: false
  }));

  try {
    for (const file of backups) {
      try {
        await fs.rename(file.target, file.backup);
      } catch (error) {
        if (error.code !== 'ENOENT') throw error;
      }
    }
    for (const file of backups) {
      await fs.rename(file.stage, file.target);
      file.published = true;
    }
  } catch (error) {
    for (const file of [...backups].reverse()) {
      if (file.published) await fs.unlink(file.target).catch(() => {});
      try {
        await fs.rename(file.backup, file.target);
      } catch (restoreError) {
        if (restoreError.code !== 'ENOENT') console.error(`恢复 ${file.target} 失败：${restoreError.message}`);
      }
    }
    throw error;
  }

  for (const file of backups) {
    await fs.unlink(file.backup).catch(() => {});
  }
}

async function cleanupStage(directory, stageNews, stageRss, moved) {
  if (!moved) {
    await fs.unlink(stageNews).catch(() => {});
    await fs.unlink(stageRss).catch(() => {});
  }
  await fs.rmdir(directory).catch(() => {});
}

async function translateAndPublish(rawReport) {
  const stageDirectory = await fs.mkdtemp(path.join(path.dirname(newsPath), '.game-intel-stage-'));
  const stageNews = path.join(stageDirectory, 'news.json');
  const stageRss = path.join(stageDirectory, 'rss.xml');
  await fs.writeFile(stageNews, `${JSON.stringify(rawReport, null, 2)}\n`, 'utf8');

  const python = process.env.PYTHON_EXECUTABLE || (process.platform === 'win32' ? 'python' : 'python3');
  const translation = spawnSync(python, ['translate_news.py'], {
    cwd: root,
    env: {
      ...process.env,
      NEWS_FILE: stageNews,
      RSS_FILE: stageRss,
      SITE_URL: siteUrl
    },
    encoding: 'utf8',
    maxBuffer: 20 * 1024 * 1024
  });
  if (translation.stdout) process.stdout.write(translation.stdout);
  if (translation.stderr) process.stderr.write(translation.stderr);

  if (translation.error || translation.status !== 0) {
    await cleanupStage(stageDirectory, stageNews, stageRss, false);
    throw new Error(`五语翻译失败：${translation.error?.message || `退出码 ${translation.status}`}`);
  }

  await replacePublishedFiles([
    { stage: stageNews, target: newsPath },
    { stage: stageRss, target: rssPath }
  ]);
  await cleanupStage(stageDirectory, stageNews, stageRss, true);
  return JSON.parse(await fs.readFile(newsPath, 'utf8'));
}

function buildEmail(report) {
  const articles = report.articles || [];
  const date = new Intl.DateTimeFormat('zh-HK', {
    timeZone: config['默认时区'],
    dateStyle: 'medium'
  }).format(new Date());
  const rows = articles.map(article => {
    const localized = translationRecord(article, 'zh-CN');
    return `<tr><td style="padding:14px;border-bottom:1px solid #e8e8e8"><strong>${escapeHtml(localized.title)}</strong><br><span style="color:#666;font-size:13px">${escapeHtml(localized.summary)}</span><br><span style="font-size:12px;color:#777">${escapeHtml(article.source)}</span><br><a href="${escapeAttr(article.url)}">阅读原文</a></td></tr>`;
  }).join('');
  return `<!doctype html><html lang="zh-CN"><meta charset="utf-8"><body style="font-family:Arial,'Microsoft YaHei',sans-serif;color:#222;max-width:760px;margin:auto"><h1>游闻 · 多语言游戏新闻</h1><p>${date} · 过去 ${report.periodHours} 小时 · 收录 ${articles.length} 条</p>${siteUrl ? `<p><a href="${escapeAttr(siteUrl)}">打开新闻站</a></p>` : ''}${articles.length ? `<table style="width:100%;border-collapse:collapse">${rows}</table>` : '<p>过去 24 小时内没有新的游戏新闻。</p>'}<p style="font-size:12px;color:#777">内容来自来源页面的公开标题与摘要，机器翻译仅供阅读，具体信息请核对原文。</p></body></html>`;
}

async function sendEmail(report) {
  if (process.env.SEND_EMAIL !== 'true') return;
  if (!process.env.GMAIL_USER || !process.env.GMAIL_APP_PASSWORD || !process.env.RECIPIENT_EMAIL) {
    console.warn('未发送邮件：请配置发件邮箱、应用专用密码和收件地址。');
    return;
  }
  const { default: nodemailer } = await import('nodemailer');
  const transporter = nodemailer.createTransport({
    host: 'smtp.gmail.com',
    port: 465,
    secure: true,
    auth: { user: process.env.GMAIL_USER, pass: process.env.GMAIL_APP_PASSWORD }
  });
  const date = new Intl.DateTimeFormat('zh-HK', {
    timeZone: config['默认时区'],
    dateStyle: 'medium'
  }).format(new Date());
  await transporter.sendMail({
    from: process.env.GMAIL_USER,
    to: process.env.RECIPIENT_EMAIL,
    subject: `游闻游戏新闻｜${date}`,
    html: buildEmail(report)
  });
  console.log(`简报已发送至 ${process.env.RECIPIENT_EMAIL}`);
}

let scraplingReport;
try {
  scraplingReport = await readScraplingReport();
} catch (error) {
  const checkedAt = new Date().toISOString();
  await writeCollectSummary({
    status: 'failed',
    checkedAt,
    error: error.message,
    articleCount: 0,
    last24hCount: 0,
    todayHktCount: 0,
    sourceCount: 0,
    successfulSourceCount: 0,
    failedSources: []
  });
  console.error(error.message);
  process.exit(1);
}

const sourceResults = normalizeSourceResults(scraplingReport);
const unique = new Map();
for (const article of (scraplingReport.articles || []).sort((left, right) => right.heatScore - left.heatScore)) {
  const key = article.url.replace(/\/$/, '').toLowerCase();
  if (!unique.has(key)) unique.set(key, article);
}

let articles = sortArticlesByHeat([...unique.values()]);
const checkedAt = new Date().toISOString();
let freshnessSummary;
try {
  freshnessSummary = assertFreshArticles(articles, {
    now: new Date(checkedAt),
    sourceResults
  });
} catch (error) {
  await writeCollectSummary({
    status: 'failed',
    checkedAt,
    error: error.message,
    ...summarizeArticles(articles, { now: new Date(checkedAt), sourceResults })
  });
  console.error(error.message);
  process.exitCode = 1;
}

if (freshnessSummary) {
  const previous = await readPreviousReport();
  const rawReport = {
    generatedAt: checkedAt,
    timezone: config['默认时区'],
    periodHours: config['采集规则']['时间范围小时'],
    sourceCount: sourceResults.length,
    articles
  };

  if (previous?.schemaVersion === 2 && signatureOf(previous) === signatureOf(rawReport)) {
    await writeCollectSummary({
      status: 'unchanged',
      checkedAt,
      ...summarizeArticles(articles, { now: new Date(checkedAt), sourceResults })
    });
    console.log(`新闻无变化：${articles.length} 条，保留上次五语数据。`);
  } else {
    const report = await translateAndPublish(rawReport);
    articles = report.articles;
    await writeCollectSummary({
      status: 'ok',
      checkedAt,
      ...summarizeArticles(articles, { now: new Date(checkedAt), sourceResults })
    });
    await sendEmail(report);
    console.log(`采集完成：${articles.length} 条新闻，五语数据与简体中文 RSS 已发布。`);
  }
}
