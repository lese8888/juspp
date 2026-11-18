import http from 'node:http';
import fs from 'node:fs/promises';
import path from 'node:path';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const root = path.dirname(fileURLToPath(import.meta.url));
const port = Number(process.env.PORT || 8765);
const intervalMs = Number(process.env.CRAWL_INTERVAL_MS || 24 * 60 * 60 * 1000);
const newsFile = process.env.NEWS_FILE || path.join(root, 'news.json');
let collecting = false;

async function collect() {
  if (collecting) return;
  collecting = true;
  console.log(`[${new Date().toISOString()}] 开始采集 RSS 新闻`);
  const child = spawn(process.execPath, ['collect.mjs'], {
    cwd: root,
    stdio: 'inherit',
    env: { ...process.env, SEND_EMAIL: 'false', NEWS_FILE: newsFile }
  });
  child.on('error', error => console.error('启动采集器失败：', error.message));
  child.on('close', code => {
    collecting = false;
    console.log(`采集进程结束，退出码：${code}`);
  });
}

const contentTypes = {
  '.html': 'text/html; charset=utf-8',
  '.json': 'application/json; charset=utf-8'
};

const server = http.createServer(async (request, response) => {
  const pathname = new URL(request.url, `http://${request.headers.host}`).pathname;
  const relativePath = pathname === '/' ? 'index.html' : pathname.replace(/^\/+/, '');
  if (!['index.html', 'news.json', 'api/news.json', 'rss.xml'].includes(relativePath)) {
    response.writeHead(404).end('未找到');
    return;
  }

  try {
    let body;
    if (relativePath === 'api/news.json' || relativePath === 'rss.xml') {
      try {
        body = await fs.readFile(newsFile);
      } catch {
        body = await fs.readFile(path.join(root, 'news.json'));
      }
      if (relativePath === 'rss.xml') {
        const report = JSON.parse(body.toString('utf8'));
        const siteUrl = `${request.headers['x-forwarded-proto'] || 'http'}://${request.headers.host}`;
        const items = (report.articles || []).map(item => `
    <item>
      <title>${xmlEscape(item.title)}</title>
      <link>${xmlEscape(item.url)}</link>
      <description>${xmlEscape(item.summary || '')}</description>
      <pubDate>${new Date(item.timestamp).toUTCString()}</pubDate>
      <guid isPermaLink="true">${xmlEscape(item.url)}</guid>
      <category>${xmlEscape(item.category || '游戏新闻')}</category>
    </item>`).join('');
        body = Buffer.from(`<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>游闻 · 游戏新闻情报</title>
  <link>${xmlEscape(siteUrl)}</link>
  <description>过去 24 小时游戏新闻聚合</description>
  <language>zh-CN</language>${items}
</channel></rss>`);
        response.writeHead(200, { 'content-type': 'application/rss+xml; charset=utf-8', 'cache-control': 'no-store' });
        response.end(body);
        return;
      }
    } else {
      body = await fs.readFile(path.join(root, relativePath));
    }
    response.writeHead(200, {
      'content-type': contentTypes[path.extname(relativePath === 'api/news.json' ? 'news.json' : relativePath)],
      'cache-control': 'no-store'
    });
    response.end(body);
  } catch {
    response.writeHead(503, { 'content-type': 'text/plain; charset=utf-8' });
    response.end('新闻数据尚未生成，请稍后刷新。');
  }
});

function xmlEscape(value) {
  return String(value).replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&apos;' })[char]);
}

server.listen(port, '0.0.0.0', () => {
  console.log(`游戏新闻情报台：http://localhost:${port}`);
  console.log(`RSS 采集间隔：${Math.round(intervalMs / 3600000)} 小时`);
  collect();
  setInterval(collect, intervalMs);
});

