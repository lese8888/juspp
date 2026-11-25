function hashText(value) {
  let hash = 2166136261;
  for (const character of String(value)) {
    hash ^= character.codePointAt(0);
    hash = Math.imul(hash, 16777619);
  }
  return hash >>> 0;
}

export const SUPPORTED_NEWS_SCHEMA_VERSIONS = Object.freeze([2, 3]);
export const CONTENT_POLICIES = Object.freeze(['full_l10n', 'digest']);

export function isSupportedNewsSchemaVersion(value) {
  return SUPPORTED_NEWS_SCHEMA_VERSIONS.includes(Number(value));
}

export function resolveContentPolicy(article = {}) {
  const translationInfo =
    article.translationInfo && typeof article.translationInfo === 'object'
      ? article.translationInfo
      : {};
  const requested = article.contentPolicy || translationInfo.contentPolicy;
  if (CONTENT_POLICIES.includes(requested)) {
    return {
      policy: requested,
      reason:
        article.contentPolicyReason ||
        translationInfo.contentPolicyReason ||
        (requested === 'digest'
          ? 'long_article_digest'
          : 'normal_length')
    };
  }
  return {
    policy: 'full_l10n',
    reason: 'legacy_content_without_policy'
  };
}

export function normalizeNewsDocument(document) {
  if (!document || typeof document !== 'object' || Array.isArray(document)) {
    throw new TypeError('新闻数据顶层必须是对象');
  }
  if (!isSupportedNewsSchemaVersion(document.schemaVersion)) {
    throw new TypeError(`不支持的新闻 schemaVersion：${document.schemaVersion}`);
  }
  if (!Array.isArray(document.articles)) {
    throw new TypeError('新闻数据缺少 articles 数组');
  }

  const translation =
    document.translation && typeof document.translation === 'object'
      ? document.translation
      : null;
  const translatedEngine = translation?.engine
    ? translation.model
      ? `${translation.engine} (${translation.model})`
      : String(translation.engine)
    : null;
  const articles = document.articles.map((article, index) => {
    if (!article || typeof article !== 'object' || Array.isArray(article)) {
      throw new TypeError(`articles[${index}] 必须是对象`);
    }
    const resolved = resolveContentPolicy(article);
    return {
      ...article,
      contentPolicy: resolved.policy,
      contentPolicyReason: resolved.reason
    };
  });

  return {
    ...document,
    translationEngine: document.translationEngine || translatedEngine || null,
    articles
  };
}

export function articleHeatScore(article) {
  const timestamp = Number(article.timestamp) || 0;
  const officialBoost = article.sourceTypeKey === 'official' ? 6 * 60 * 60 * 1000 : 0;
  const summaryDepth = Math.min(String(article.originalSummary || '').length, 1200) * 100;
  return Math.round(timestamp + officialBoost + hashText(article.originalTitle || '') + summaryDepth);
}

export function sortArticlesByHeat(articles) {
  return [...articles].sort((left, right) => {
    const heatDifference = (Number(right.heatScore) || 0) - (Number(left.heatScore) || 0);
    if (heatDifference) return heatDifference;
    const timeDifference = (Number(right.timestamp) || 0) - (Number(left.timestamp) || 0);
    if (timeDifference) return timeDifference;
    return String(left.url).localeCompare(String(right.url));
  });
}

export function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, character => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;'
  })[character]);
}

export function escapeAttr(value) {
  return escapeHtml(value).replace(/`/g, '&#96;');
}

export function escapeXml(value) {
  return String(value).replace(/[&<>"']/g, character => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&apos;'
  })[character]);
}
