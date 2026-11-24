import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import {
  findTranslationMarker,
  translationMarkerContext
} from '../lib/translation-marker.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const newsPath = process.env.NEWS_FILE
  ? path.resolve(process.env.NEWS_FILE)
  : path.join(root, 'news.json');
const reportPath = process.env.TRANSLATION_MARKER_REPORT
  ? path.resolve(process.env.TRANSLATION_MARKER_REPORT)
  : path.join(os.tmpdir(), 'youwen-translation-marker-report.json');
const languages = ['zh-CN', 'zh-TW', 'ja', 'ko'];
const fields = ['title', 'summary', 'content'];

const report = JSON.parse(await fs.readFile(newsPath, 'utf8'));
const issues = [];

for (const article of report.articles || []) {
  for (const language of languages) {
    const localized = article.translations?.[language] || {};
    for (const field of fields) {
      const value = localized[field] || '';
      const match = findTranslationMarker(value);
      if (!match) continue;
      issues.push({
        url: article.url,
        title: article.originalTitle,
        language,
        field,
        marker: match.marker,
        context: translationMarkerContext(value, match.marker)
      });
    }
  }
}

const diagnostic = {
  generatedAt: report.generatedAt || null,
  markerCount: issues.length,
  issues
};

await fs.mkdir(path.dirname(reportPath), { recursive: true });
await fs.writeFile(reportPath, `${JSON.stringify(diagnostic, null, 2)}\n`, 'utf8');

if (issues.length === 0) {
  console.log('通过：机器翻译结果没有内部术语占位符。');
  process.exit(0);
}

console.error(`失败：发现 ${issues.length} 处内部翻译标记，诊断文件：${reportPath}`);
for (const issue of issues.slice(0, 20)) {
  console.error(
    `- ${issue.language}/${issue.field} ${issue.marker}：` +
    `${issue.title || '无标题'}（${issue.url || '无链接'}）`
  );
}
process.exit(1);
