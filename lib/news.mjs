const NAMED_ENTITIES = new Map(Object.entries({
  amp: '&',
  apos: "'",
  bull: '•',
  copy: '©',
  gt: '>',
  hellip: '…',
  ldquo: '“',
  lsquo: '‘',
  lt: '<',
  mdash: '—',
  middot: '·',
  nbsp: ' ',
  ndash: '–',
  quot: '"',
  rdquo: '”',
  reg: '®',
  rsquo: '’',
  trade: '™'
}));

const RSS_FOOTER_RE = /\s*(?:(?:read|continue reading)\s+(?:the\s+)?(?:full\s+)?(?:article|story|post)(?:\s+(?:on|at)\s+[A-Za-z0-9.-]+)?|read more(?:\s+(?:on|at)\s+[A-Za-z0-9.-]+)?)\s*\.?\s*$/i;

export function decodeHtmlEntities(value = '') {
  let decoded = String(value);
  for (let pass = 0; pass < 3; pass += 1) {
    const next = decoded
      .replace(/&#(\d+);/g, (_, code) => String.fromCodePoint(Number(code)))
      .replace(/&#x([\da-f]+);/gi, (_, code) => String.fromCodePoint(parseInt(code, 16)))
      .replace(/&([a-z][a-z\d]+);/gi, (entity, name) => NAMED_ENTITIES.get(name.toLowerCase()) ?? entity);
    if (next === decoded) break;
    decoded = next;
  }
  return decoded;
}

export function cleanRssText(value = '') {
  let text = String(value)
    .replace(/<!\[CDATA\[([\s\S]*?)\]\]>/g, '$1')
    .replace(/<\s*br\s*\/?>/gi, ' ')
    .replace(/<\/?(?:p|div|li|ul|ol|h[1-6])\b[^>]*>/gi, ' ')
    .replace(/<[^>]*>/g, ' ');

  text = decodeHtmlEntities(text)
    .replace(/\u00a0/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();

  return text.replace(RSS_FOOTER_RE, '').trim();
}

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

export function escapeXml(value) {
  return String(value).replace(/[&<>"']/g, character => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&apos;'
  })[character]);
}
