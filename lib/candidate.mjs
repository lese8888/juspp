export function normalizeArticleUrl(value) {
  return String(value || '').trim().replace(/\/+$/, '').toLowerCase();
}

export function selectNewArticles(previousReport, collectedReport) {
  const knownUrls = new Set(
    (previousReport?.articles || [])
      .map(article => normalizeArticleUrl(article?.url))
      .filter(Boolean)
  );

  return (collectedReport?.articles || []).filter(article => {
    const url = normalizeArticleUrl(article?.url);
    return Boolean(url) && !knownUrls.has(url);
  });
}
