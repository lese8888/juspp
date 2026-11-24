import assert from 'node:assert/strict';
import test from 'node:test';

import { contentTypeMatches, resolveRoute } from '../cloudflare/worker.mjs';

test('Cloudflare Worker 提供页面、新闻 JSON 别名与 RSS', () => {
  assert.deepEqual(resolveRoute('/'), {
    path: '/index.html',
    contentType: 'text/html; charset=utf-8'
  });
  assert.equal(resolveRoute('/news.json').path, '/news.json');
  assert.equal(resolveRoute('/api/news.json').path, '/news.json');
  assert.equal(resolveRoute('/rss.xml').contentType, 'application/rss+xml; charset=utf-8');
  assert.equal(resolveRoute('/unknown'), null);
});

test('Cloudflare Worker 接受 JSON、HTML 与合法 RSS XML 类型', () => {
  assert.equal(
    contentTypeMatches(resolveRoute('/api/news.json'), 'application/json; charset=utf-8'),
    true
  );
  assert.equal(
    contentTypeMatches(resolveRoute('/'), 'text/html; charset=utf-8'),
    true
  );
  assert.equal(
    contentTypeMatches(resolveRoute('/rss.xml'), 'application/xml; charset=utf-8'),
    true
  );
  assert.equal(contentTypeMatches(resolveRoute('/rss.xml'), 'text/html'), false);
});
