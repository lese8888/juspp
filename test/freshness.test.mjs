import assert from 'node:assert/strict';
import test from 'node:test';

import {
  assertFreshArticles,
  isPublishedTodayInHongKong,
  summarizeArticles
} from '../lib/freshness.mjs';
import { renderCollectSummary } from '../lib/collect-summary.mjs';

const NOW = new Date('2026-11-24T02:00:00.000Z');
const article = (timestamp, title) => ({ timestamp, originalTitle: title });

test('香港当天判断跨午夜正确', () => {
  const justAfterMidnight = article(Date.parse('2026-11-23T16:01:00.000Z'), '今日');
  const justBeforeMidnight = article(Date.parse('2026-11-23T15:59:00.000Z'), '昨日');
  assert.equal(isPublishedTodayInHongKong(justAfterMidnight, NOW), true);
  assert.equal(isPublishedTodayInHongKong(justBeforeMidnight, NOW), false);
});

test('当天为空但仍有过去 24 小时新闻时允许采集', () => {
  const articles = [article(Date.parse('2026-11-23T15:00:00.000Z'), '昨日新闻')];
  const summary = assertFreshArticles(articles, { now: NOW });
  assert.equal(summary.todayHktCount, 0);
  assert.equal(summary.last24hCount, 1);
});

test('超过 26 小时的旧数据被阻断', () => {
  const articles = [article(Date.parse('2026-11-22T23:59:00.000Z'), '旧闻')];
  assert.throws(
    () => assertFreshArticles(articles, { now: NOW }),
    /超过 26 小时上限/
  );
});

test('所有来源失败时给出明确原因', () => {
  assert.throws(
    () => assertFreshArticles([], {
      now: NOW,
      sourceResults: [
        { name: 'A', ok: false, error: 'HTTP 500' },
        { name: 'B', ok: false, error: 'timeout' }
      ]
    }),
    /所有新闻源均连接失败/
  );
});

test('摘要统计当天、24 小时和来源状态', () => {
  const summary = summarizeArticles([
    article(Date.parse('2026-11-23T16:30:00.000Z'), '今日'),
    article(Date.parse('2026-11-23T12:00:00.000Z'), '昨日')
  ], {
    now: NOW,
    sourceResults: [
      { name: 'A', ok: true },
      { name: 'B', ok: false, error: 'timeout' }
    ]
  });
  assert.equal(summary.todayHktCount, 1);
  assert.equal(summary.last24hCount, 2);
  assert.equal(summary.successfulSourceCount, 1);
  assert.equal(summary.failedSources.length, 1);
});

test('Actions 摘要包含失败来源', () => {
  const markdown = renderCollectSummary({
    status: 'failed',
    checkedAt: '2026-11-24T02:00:00.000Z',
    articleCount: 0,
    last24hCount: 0,
    todayHktCount: 0,
    todayHktDate: '2026-11-24',
    latestPublishedAt: null,
    successfulSourceCount: 0,
    sourceCount: 1,
    error: '所有新闻源均连接失败',
    failedSources: [{ name: 'Example', error: 'timeout' }]
  });
  assert.match(markdown, /结果：失败/);
  assert.match(markdown, /Example：timeout/);
});
