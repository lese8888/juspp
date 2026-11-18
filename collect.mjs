import fs from 'node:fs/promises';

const config = JSON.parse(await fs.readFile(new URL('./sources.json', import.meta.url), 'utf8'));
const newsFile = process.env.NEWS_FILE || new URL('./news.json', import.meta.url);
const activeSources = config['来源'].filter(source => source['启用'] && source['订阅']);
const cutoff = Date.now() - config['采集规则']['时间范围小时'] * 60 * 60 * 1000;
let failedSourceCount = 0;

function clean(value = '') {
  return value
    .replace(/<!\[CDATA\[([\s\S]*?)\]\]>/g, '$1')
    .replace(/<[^>]*>/g, ' ')
    .replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"').replace(/&#39;|&apos;/g, "'")
    .replace(/&#(\d+);/g, (_, code) => String.fromCodePoint(Number(code)))
    .replace(/&#x([\da-f]+);/gi, (_, code) => String.fromCodePoint(parseInt(code, 16)))
    .replace(/\s+/g, ' ').trim();
}

function tag(block, name) {
  const match = block.match(new RegExp(`<${name}(?:\\s[^>]*)?>([\\s\\S]*?)<\\/${name}>`, 'i'));
  return match?.[1] ?? '';
}

async function readSource(source) {
  try {
    const response = await fetch(source['订阅'], { headers: { 'user-agent': 'GameIntelBrief/1.0' }, signal: AbortSignal.timeout(15000) });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const xml = await response.text();
    const blocks = [...xml.matchAll(/<(item|entry)(?:\s[^>]*)?>[\s\S]*?<\/\1>/gi)].map(match => match[0]);
    return blocks.map(block => {
      const linkTag = block.match(/<link\b([^>]*)\/?\s*>/i);
      const linkAttr = linkTag?.[1]?.match(/href=["']([^"']+)["']/i)?.[1];
      const date = clean(tag(block, 'pubDate') || tag(block, 'published') || tag(block, 'updated') || tag(block, 'dc:date'));
      return {
        title: clean(tag(block, 'title')),
        url: clean(tag(block, 'link')) || linkAttr || clean(tag(block, 'guid')),
        summary: clean(tag(block, 'description') || tag(block, 'summary') || tag(block, 'content:encoded')).slice(0, 420),
        publishedAt: date,
        timestamp: Date.parse(date) || 0,
        source: source['名称'],
        sourceType: source['类型'],
        category: source['分类']
      };
    }).filter(item => item.title && item.url && item.timestamp >= cutoff);
  } catch (error) {
    failedSourceCount += 1;
    console.error(`${source['名称']}: ${error.message}`);
    return [];
  }
}

const batches = await Promise.all(activeSources.map(readSource));
const unique = new Map();
for (const item of batches.flat().sort((a, b) => b.timestamp - a.timestamp)) {
  const key = item.url.replace(/\/$/, '').toLowerCase();
  if (!unique.has(key)) unique.set(key, item);
}
let articles = [...unique.values()].sort((a, b) => b.timestamp - a.timestamp);
let previousReport;
try {
  previousReport = JSON.parse(await fs.readFile(newsFile, 'utf8'));
} catch {}
if (articles.length === 0 && failedSourceCount === activeSources.length) {
  console.error('所有新闻源均连接失败，保留上一份新闻数据。');
  articles = previousReport?.articles || [];
}
const unchanged = previousReport
  && previousReport.sourceCount === activeSources.length
  && JSON.stringify(previousReport.articles) === JSON.stringify(articles);
const report = {
  generatedAt: unchanged ? previousReport.generatedAt : new Date().toISOString(),
  timezone: config['默认时区'],
  periodHours: config['采集规则']['时间范围小时'],
  sourceCount: activeSources.length,
  articles
};
await fs.writeFile(newsFile, `${JSON.stringify(report, null, 2)}\n`);
const rssSiteUrl = process.env.SITE_URL || 'https://lese8888.github.io/juspp/';
const rssItems = articles.map(item => `
    <item>
      <title>${escapeXml(item.title)}</title>
      <link>${escapeXml(item.url)}</link>
      <description>${escapeXml(item.summary || '')}</description>
      <pubDate>${new Date(item.timestamp).toUTCString()}</pubDate>
      <guid isPermaLink="true">${escapeXml(item.url)}</guid>
      <category>${escapeXml(item.category || '游戏新闻')}</category>
    </item>`).join('');
await fs.writeFile(new URL('./rss.xml', import.meta.url), `<?xml version="1.0" encoding="UTF-8"?>\n<rss version="2.0"><channel>\n  <title>游闻 · 游戏新闻情报</title>\n  <link>${escapeXml(rssSiteUrl)}</link>\n  <description>过去 24 小时游戏新闻聚合</description>\n  <language>zh-CN</language>${rssItems}\n</channel></rss>\n`);

const byCategory = Map.groupBy ? Map.groupBy(articles, item => item.category) : articles.reduce((map, item) => map.set(item.category, [...(map.get(item.category) || []), item]), new Map());
const date = new Intl.DateTimeFormat('zh-HK', { timeZone: config['默认时区'], dateStyle: 'medium' }).format(new Date());
const rows = articles.map(item => `<tr><td style="padding:14px;border-bottom:1px solid #e8e8e8"><strong>${escapeHtml(item.title)}</strong><br><span style="color:#666;font-size:13px">${escapeHtml(item.summary)}</span><br><span style="font-size:12px;color:#777">${escapeHtml(item.category)} · ${escapeHtml(item.source)}</span><br><a href="${escapeAttr(item.url)}">阅读原文</a></td></tr>`).join('');
const siteUrl = process.env.SITE_URL || '';
const htmlBody = `<!doctype html><html lang="zh-CN"><meta charset="utf-8"><body style="font-family:Arial,'Microsoft YaHei',sans-serif;color:#222;max-width:760px;margin:auto"><h1>游戏情报早报</h1><p>${date} · 过去 ${report.periodHours} 小时 · 收录 ${articles.length} 条</p>${siteUrl ? `<p><a href="${escapeAttr(siteUrl)}">打开可视化情报台</a></p>` : ''}${articles.length ? `<table style="width:100%;border-collapse:collapse">${rows}</table>` : '<p>过去 24 小时内，已启用 RSS 来源没有新文章。</p>'}<p style="font-size:12px;color:#777">自动收集稿件，请发布前核对来源原文及传闻信息。</p></body></html>`;

function escapeHtml(value) { return String(value).replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char]); }
function escapeAttr(value) { return escapeHtml(value).replace(/`/g, '&#96;'); }
function escapeXml(value) { return String(value).replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&apos;' })[char]); }

if (process.env.SEND_EMAIL === 'true') {
  if (!process.env.GMAIL_USER || !process.env.GMAIL_APP_PASSWORD || !process.env.RECIPIENT_EMAIL) {
    console.warn('未发送邮件：请在 GitHub Secrets 配置发件邮箱、应用专用密码和收件地址。');
  } else {
    const { default: nodemailer } = await import('nodemailer');
    const transporter = nodemailer.createTransport({ host: 'smtp.gmail.com', port: 465, secure: true, auth: { user: process.env.GMAIL_USER, pass: process.env.GMAIL_APP_PASSWORD } });
    await transporter.sendMail({ from: process.env.GMAIL_USER, to: process.env.RECIPIENT_EMAIL, subject: `游戏情报早报｜${date}`, html: htmlBody });
    console.log(`简报已发送至 ${process.env.RECIPIENT_EMAIL}`);
  }
}
console.log(`采集完成：${articles.length} 条新闻，${byCategory.size} 个分类。`);

