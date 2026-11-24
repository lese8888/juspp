import assert from 'node:assert/strict';
import test from 'node:test';

import {
  articleHeatScore,
  escapeAttr,
  sortArticlesByHeat
} from '../lib/news.mjs';

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

test('邮件链接属性转义包含反引号', () => {
  assert.equal(escapeAttr('https://example.com/?q=`x`&a=1'), 'https://example.com/?q=&#96;x&#96;&amp;a=1');
});
