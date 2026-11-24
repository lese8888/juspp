import assert from 'node:assert/strict';
import test from 'node:test';

import { findTranslationMarker } from '../lib/translation-marker.mjs';

test('检测中文紧邻的内部翻译占位符', () => {
  assert.equal(
    findTranslationMarker('新闻QZABCDEF消息')?.marker,
    'QZABCDEF'
  );
});

test('检测被拆分的内部翻译占位符', () => {
  assert.equal(
    findTranslationMarker('新闻QZAB-CD-EF消息')?.marker,
    'QZAB-CD-EF'
  );
});

test('正常数字双方括号不会被当作内部占位符', () => {
  assert.equal(findTranslationMarker('引用编号 [[123456]]'), null);
});
