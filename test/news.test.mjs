import assert from 'node:assert/strict';
import test from 'node:test';

import {
  articleHeatScore,
  cleanRssText,
  decodeHtmlEntities,
  sortArticlesByHeat
} from '../lib/news.mjs';

test('清理 HTML、实体与 RSS 固定尾注', () => {
  const input = '<p>Tom &amp; Jerry&hellip; &nbsp;<strong>Switch</strong></p> Read the full article on example.com';
  assert.equal(cleanRssText(input), 'Tom & Jerry… Switch');
});

test('解码常见与数字 HTML 实体', () => {
  assert.equal(decodeHtmlEntities('A&nbsp;B &#8212; C &#x1F3AE; &unknown;'), 'A B — C 🎮 &unknown;');
});

test('完整保留长摘要而不截断', () => {
  const longSummary = `${'这是一个完整摘要。'.repeat(180)} Read more at example.com`;
  const cleaned = cleanRssText(longSummary);
  assert.ok(cleaned.length > 1500);
  assert.ok(cleaned.endsWith('这是一个完整摘要。'));
});

test('官方来源与完整摘要获得稳定热度加权', () => {
  const base = {
    timestamp: 1_800_000_000_000,
    originalTitle: 'Same title',
    originalSummary: 'Same summary',
    sourceTypeKey: 'media'
  };
  const official = { ...base, sourceTypeKey: 'official' };
  assert.ok(articleHeatScore(official) > articleHeatScore(base));
  assert.equal(articleHeatScore(official), articleHeatScore({ ...official }));
});

test('排序只依赖新闻数据中的 heatScore', () => {
  const sorted = sortArticlesByHeat([
    { url: 'a', heatScore: 10, timestamp: 1 },
    { url: 'b', heatScore: 20, timestamp: 1 },
    { url: 'c', heatScore: 20, timestamp: 2 }
  ]);
  assert.deepEqual(sorted.map(item => item.url), ['c', 'b', 'a']);
});
