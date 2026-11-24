import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { assertFreshArticles } from '../lib/freshness.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const newsPath = process.env.NEWS_FILE
  ? path.resolve(process.env.NEWS_FILE)
  : path.join(root, 'news.json');
const report = JSON.parse(await fs.readFile(newsPath, 'utf8'));
const summary = assertFreshArticles(report.articles || [], { now: new Date() });
console.log(
  `新鲜度通过：最新原文 ${summary.latestPublishedAt}，过去 24 小时 ${summary.last24hCount} 条，香港当天 ${summary.todayHktCount} 条。`
);
