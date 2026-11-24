import http from 'node:http';
import fs from 'node:fs/promises';
import path from 'node:path';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const root = path.dirname(fileURLToPath(import.meta.url));
const port = Number(process.env.PORT || 8765);
const intervalMs = Number(process.env.CRAWL_INTERVAL_MS || 24 * 60 * 60 * 1000);
const newsFile = process.env.NEWS_FILE || path.join(root, 'news.json');
const rssFile = process.env.RSS_FILE || path.join(root, 'rss.xml');
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
    if (relativePath === 'api/news.json') {
      body = await fs.readFile(newsFile);
    } else if (relativePath === 'rss.xml') {
      body = await fs.readFile(rssFile);
      response.writeHead(200, {
        'content-type': 'application/rss+xml; charset=utf-8',
        'cache-control': 'no-store'
      });
      response.end(body);
      return;
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

server.listen(port, '0.0.0.0', () => {
  console.log(`游戏新闻情报台：http://localhost:${port}`);
  console.log(`RSS 采集间隔：${Math.round(intervalMs / 3600000)} 小时`);
  collect();
  setInterval(collect, intervalMs);
});
