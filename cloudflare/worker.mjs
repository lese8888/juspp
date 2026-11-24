const ORIGIN = 'https://lese8888.github.io/juspp/';
const CACHE_VERSION = '2';
const CACHE_SECONDS = 300;

const ROUTES = new Map([
  ['/', { path: '/index.html', contentType: 'text/html; charset=utf-8' }],
  ['/index.html', { path: '/index.html', contentType: 'text/html; charset=utf-8' }],
  ['/news.json', { path: '/news.json', contentType: 'application/json; charset=utf-8' }],
  ['/api/news.json', { path: '/news.json', contentType: 'application/json; charset=utf-8' }],
  ['/rss.xml', { path: '/rss.xml', contentType: 'application/rss+xml; charset=utf-8' }]
]);

export function resolveRoute(pathname) {
  return ROUTES.get(pathname) || null;
}

function responseHeaders(asset, cacheStatus) {
  return {
    'content-type': asset.contentType,
    'cache-control': `public, max-age=${CACHE_SECONDS}, s-maxage=${CACHE_SECONDS}, stale-while-revalidate=60`,
    'access-control-allow-origin': '*',
    'referrer-policy': 'strict-origin-when-cross-origin',
    'x-content-type-options': 'nosniff',
    'x-youwen-cache': cacheStatus
  };
}

export function contentTypeMatches(asset, actualType) {
  if (asset.path === '/rss.xml') {
    return /^(?:application|text)\/(?:rss\+)?xml(?:;|$)/i.test(actualType);
  }
  return actualType.startsWith(asset.contentType.split(';', 1)[0]);
}

export default {
  async fetch(request) {
    const url = new URL(request.url);
    const asset = resolveRoute(url.pathname);
    if (!asset) {
      return new Response('未找到', {
        status: 404,
        headers: { 'content-type': 'text/plain; charset=utf-8' }
      });
    }

    const cache = caches.default;
    const originUrl = new URL(
      `${asset.path.replace(/^\//, '')}?cache=${CACHE_VERSION}`,
      ORIGIN
    );
    const cacheRequest = new Request(originUrl, { method: 'GET' });
    let cached = await cache.match(cacheRequest);
    let cacheStatus = 'HIT';

    if (!cached) {
      cacheStatus = 'MISS';
      let originResponse;
      try {
        originResponse = await fetch(originUrl, {
          headers: {
            accept: '*/*',
            'user-agent': 'GameIntelBrief-Cloudflare/1.0'
          },
          cf: {
            cacheEverything: true,
            cacheTtl: CACHE_SECONDS
          }
        });
      } catch (error) {
        console.error('上游请求异常', originUrl.toString(), error.message);
        return new Response('上游新闻源暂不可用', {
          status: 502,
          headers: { 'content-type': 'text/plain; charset=utf-8' }
        });
      }
      if (!originResponse.ok) {
        console.error(
          '上游响应失败',
          originUrl.toString(),
          originResponse.status,
          originResponse.statusText
        );
        return new Response('上游新闻源暂不可用', {
          status: 502,
          headers: { 'content-type': 'text/plain; charset=utf-8' }
        });
      }
      const actualType = originResponse.headers.get('content-type') || '';
      if (!contentTypeMatches(asset, actualType)) {
        console.error(
          '上游内容类型不匹配',
          originUrl.toString(),
          actualType,
          asset.contentType
        );
        return new Response('上游新闻源返回了异常内容', {
          status: 502,
          headers: { 'content-type': 'text/plain; charset=utf-8' }
        });
      }
      cached = new Response(originResponse.body, {
        status: 200,
        headers: responseHeaders(asset, cacheStatus)
      });
      await cache.put(cacheRequest, cached.clone());
    }

    return new Response(request.method === 'HEAD' ? null : cached.body, {
      status: 200,
      headers: responseHeaders(asset, cacheStatus)
    });
  }
};
