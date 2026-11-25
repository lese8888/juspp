import assert from 'node:assert/strict';
import test from 'node:test';

import {
  normalizeArticleUrl,
  selectNewArticles
} from '../lib/candidate.mjs';

test('URL 去重忽略大小写和末尾斜杠', () => {
  assert.equal(
    normalizeArticleUrl(' HTTPS://Example.com/Story/ '),
    'https://example.com/story'
  );
});

test('只选择冻结生产数据中不存在的新文章', () => {
  const previous = {
    schemaVersion: 2,
    articles: [
      { url: 'https://example.com/old/' },
      { url: 'https://example.com/appended' }
    ]
  };
  const collected = {
    articles: [
      { url: 'https://example.com/OLD' },
      { url: 'https://example.com/appended/' },
      { url: 'https://example.com/new' }
    ]
  };

  assert.deepEqual(selectNewArticles(previous, collected), [
    { url: 'https://example.com/new' }
  ]);
});

test('没有既有报告时把所有有效 URL 视为新文章', () => {
  const collected = {
    articles: [
      { url: 'https://example.com/a' },
      { url: '' }
    ]
  };

  assert.deepEqual(selectNewArticles(null, collected), [
    { url: 'https://example.com/a' }
  ]);
});
