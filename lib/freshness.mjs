export const HONG_KONG_TIME_ZONE = 'Asia/Hong_Kong';
export const MAX_NEWS_AGE_HOURS = 26;

export function articleTimestamp(article) {
  const numeric = Number(article?.timestamp);
  if (Number.isFinite(numeric) && numeric > 0) return numeric;
  const parsed = Date.parse(article?.publishedAt || '');
  return Number.isFinite(parsed) ? parsed : 0;
}

export function hongKongDateKey(value) {
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  return new Intl.DateTimeFormat('en-CA', {
    timeZone: HONG_KONG_TIME_ZONE,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit'
  }).format(date);
}

export function isPublishedTodayInHongKong(article, now = new Date()) {
  const timestamp = articleTimestamp(article);
  if (!timestamp) return false;
  return hongKongDateKey(timestamp) === hongKongDateKey(now);
}

export function summarizeArticles(articles, { now = new Date(), sourceResults = [] } = {}) {
  const nowTime = new Date(now).getTime();
  const validArticles = Array.isArray(articles) ? articles : [];
  const timestamps = validArticles.map(articleTimestamp).filter(Boolean);
  const latestTimestamp = timestamps.length ? Math.max(...timestamps) : 0;
  const last24hCount = timestamps.filter(timestamp => timestamp >= nowTime - 24 * 60 * 60 * 1000).length;
  const todayHktCount = validArticles.filter(article => isPublishedTodayInHongKong(article, now)).length;
  const failedSources = sourceResults
    .filter(result => !result.ok)
    .map(result => ({ name: result.name, error: result.error || '未知错误' }));

  return {
    articleCount: validArticles.length,
    last24hCount,
    todayHktCount,
    todayHktDate: hongKongDateKey(nowTime),
    latestPublishedAt: latestTimestamp ? new Date(latestTimestamp).toISOString() : null,
    latestAgeHours: latestTimestamp ? Number(((nowTime - latestTimestamp) / 3600000).toFixed(2)) : null,
    sourceCount: sourceResults.length,
    successfulSourceCount: sourceResults.filter(result => result.ok).length,
    failedSources
  };
}

export function assertFreshArticles(
  articles,
  { now = new Date(), sourceResults = [], maxAgeHours = MAX_NEWS_AGE_HOURS } = {}
) {
  const summary = summarizeArticles(articles, { now, sourceResults });
  if (!summary.articleCount) {
    if (sourceResults.length && sourceResults.every(result => !result.ok)) {
      throw new Error('所有新闻源均连接失败，已保留上一份有效数据。');
    }
    throw new Error('过去 24 小时没有可发布新闻，已保留上一份有效数据。');
  }
  if (!summary.latestPublishedAt) {
    throw new Error('新闻缺少可解析的原文发布时间，已保留上一份有效数据。');
  }
  if (summary.latestAgeHours > maxAgeHours) {
    throw new Error(
      `最新新闻距现在 ${summary.latestAgeHours} 小时，超过 ${maxAgeHours} 小时上限，已保留上一份有效数据。`
    );
  }
  return summary;
}
