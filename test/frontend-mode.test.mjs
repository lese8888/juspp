import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

import {
  normalizeNewsDocument,
  resolveContentPolicy
} from '../lib/news.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const html = await fs.readFile(path.join(root, 'index.html'), 'utf8');

test('旧 schema v2 缺失模式时安全回退为完整译文', () => {
  assert.deepEqual(resolveContentPolicy({}), {
    policy: 'full_l10n',
    reason: 'legacy_content_without_policy'
  });
});

test('schema v3 保留完整译文与内容摘要模式', () => {
  const document = normalizeNewsDocument({
    schemaVersion: 3,
    translation: { engine: 'llm', model: 'deepseek-flash' },
    articles: [
      {
        url: 'https://example.com/full',
        contentPolicy: 'full_l10n',
        contentPolicyReason: 'normal_length'
      },
      {
        url: 'https://example.com/digest',
        translationInfo: {
          contentPolicy: 'digest',
          contentPolicyReason: 'english_long_article'
        }
      }
    ]
  });

  assert.equal(document.schemaVersion, 3);
  assert.equal(document.translationEngine, 'llm (deepseek-flash)');
  assert.equal(document.articles[0].contentPolicy, 'full_l10n');
  assert.equal(document.articles[1].contentPolicy, 'digest');
  assert.equal(
    document.articles[1].contentPolicyReason,
    'english_long_article'
  );
});

test('页面包含五语模式标签、详情标签和暂停自动发布提示', () => {
  for (const marker of [
    '完整译文',
    '內容摘要',
    'Full translation',
    'Content summary',
    '完全翻訳',
    '内容要約',
    '전체 번역',
    '내용 요약'
  ]) {
    assert.ok(html.includes(marker), `缺少模式文案：${marker}`);
  }
  assert.match(html, /id="detail-mode"/);
  assert.match(html, /contentPolicyOf\(item\)/);
  assert.match(html, /自动发布暂停|自動發布暫停|automatic publishing is paused|自動公開を停止|자동 게시 중지/);
  assert.doesNotMatch(html, /id="rss-link"/);
});
