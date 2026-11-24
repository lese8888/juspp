function hashText(value) {
  let hash = 2166136261;
  for (const character of String(value)) {
    hash ^= character.codePointAt(0);
    hash = Math.imul(hash, 16777619);
  }
  return hash >>> 0;
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
