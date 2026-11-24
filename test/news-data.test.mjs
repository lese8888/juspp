import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

import { escapeXml } from '../lib/news.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const report = JSON.parse(await fs.readFile(path.join(root, 'news.json'), 'utf8'));
const rss = await fs.readFile(path.join(root, 'rss.xml'), 'utf8');
const languages = ['zh-CN', 'zh-TW', 'en', 'ja', 'ko'];

test('新闻数据使用 schemaVersion 2 与固定五语顺序', () => {
  assert.equal(report.schemaVersion, 2);
  assert.deepEqual(report.supportedLanguages, languages);
  assert.equal(report.defaultLanguage, 'zh-CN');
  assert.ok(report.articles.length > 0);
});

test('每条新闻包含合法稳定字段与完整五语标题、摘要和正文', () => {
  const urls = new Set();
  for (const article of report.articles) {
    assert.ok(article.originalTitle.trim());
    assert.ok(article.originalSummary.trim());
    assert.ok(article.originalContent.trim());
    assert.equal(article.sourceLanguage, 'en');
    assert.ok(article.sourceKey);
    assert.ok(['official', 'media'].includes(article.sourceTypeKey));
    assert.ok(article.categoryKey);
    assert.match(article.url, /^https?:\/\//);
    assert.ok(Number(article.timestamp) > 0);
    assert.ok(Number(article.heatScore) > 0);
    assert.ok(!urls.has(article.url), `重复 URL：${article.url}`);
    urls.add(article.url);
    assert.deepEqual(Object.keys(article.translations), languages);
    for (const language of languages) {
      assert.ok(article.translations[language].title.trim());
      assert.ok(article.translations[language].summary.trim());
      assert.ok(article.translations[language].content.trim());
      assert.deepEqual(
        Object.keys(article.translations[language]).sort(),
        ['content', 'summary', 'title']
      );
    }
  }
});

test('不发布旧口播、翻译状态或内部术语标记', () => {
  const serialized = JSON.stringify(report);
  assert.doesNotMatch(serialized, /"videoScript"|"translationStatus"|口播稿/);
  assert.doesNotMatch(serialized, /QZ[A-Z]{5,8}|\[\[\s*\d{5,}/);
  assert.doesNotMatch(serialized, /Read the full article|Continue reading|Read more at/i);
});

test('新闻按采集阶段 heatScore 稳定降序排列', () => {
  for (let index = 1; index < report.articles.length; index += 1) {
    assert.ok(report.articles[index - 1].heatScore >= report.articles[index].heatScore);
  }
});

test('RSS 条目数与新闻数一致且标题来自简体中文', () => {
  const itemCount = (rss.match(/<item>/g) || []).length;
  assert.equal(itemCount, report.articles.length);
  for (const article of report.articles) {
    assert.ok(rss.includes(`<title>${escapeXml(article.translations['zh-CN'].title)}</title>`));
    assert.ok(rss.includes(`<link>${escapeXml(article.url)}</link>`));
  }
  assert.match(rss, /<language>zh-CN<\/language>/);
});
