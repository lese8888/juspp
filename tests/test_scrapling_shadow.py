import unittest
from datetime import datetime, timezone

import scrapling_shadow as shadow


class ScraplingShadowTests(unittest.TestCase):
    def test_parse_sitemap_preserves_publication_date(self):
        body = """<?xml version="1.0" encoding="UTF-8"?>
        <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"
                xmlns:news="http://www.google.com/schemas/sitemap-news/0.9">
          <url>
            <loc>https://example.com/news/one</loc>
            <news:news>
              <news:publication_date>2026-11-24T03:00:00Z</news:publication_date>
              <news:title>公开新闻标题</news:title>
            </news:news>
          </url>
        </urlset>""".encode("utf-8")
        kind, entries = shadow.parse_sitemap(body)
        self.assertEqual(kind, "urlset")
        self.assertEqual(entries[0]["publication_date"], "2026-11-24T03:00:00Z")
        self.assertEqual(entries[0]["title"], "公开新闻标题")

    def test_extract_json_ld_metadata(self):
        page = """<html><head><script type="application/ld+json">
        {"@graph":[{"headline":"JSON-LD 标题",
        "datePublished":"2026-11-24T01:02:03Z",
        "description":"公开摘要内容。"}]}</script></head>
        <body><h1>备用标题</h1></body></html>""".encode("utf-8")
        metadata = shadow.extract_article_metadata(page)
        self.assertEqual(metadata["title"], "JSON-LD 标题")
        self.assertEqual(metadata["published"], "2026-11-24T01:02:03Z")
        self.assertEqual(metadata["summary"], "公开摘要内容。")

    def test_extract_json_ld_prefers_article_over_website(self):
        page = """<html><head><script type="application/ld+json">
        {"@graph":[
          {"@type":"WebSite","headline":"站点名称","dateCreated":"2010-01-01T00:00:00Z",
           "description":"站点说明"},
          {"@type":"WebPage","name":"真正的文章标题",
           "dateCreated":"2026-11-24T02:00:00Z","description":"文章摘要"}
        ]}</script></head><body></body></html>""".encode("utf-8")
        metadata = shadow.extract_article_metadata(page)
        self.assertEqual(metadata["title"], "真正的文章标题")
        self.assertEqual(metadata["published"], "2026-11-24T02:00:00Z")
        self.assertEqual(metadata["summary"], "文章摘要")

    def test_parse_datetime_normalizes_timezone(self):
        parsed = shadow.parse_datetime("2026-11-24T11:00:00+08:00")
        self.assertEqual(parsed, datetime(2026, 11, 24, 3, 0, tzinfo=timezone.utc))

    def test_resolve_monthly_sitemap_template(self):
        now = datetime(2026, 11, 24, 4, 0, tzinfo=timezone.utc)
        resolved = shadow.resolve_sitemap_url(
            "https://www.gamedeveloper.com/news/archive/{year}/{month}.xml",
            now,
        )
        self.assertEqual(
            resolved,
            "https://www.gamedeveloper.com/news/archive/2026/november.xml",
        )

    def test_heat_score_is_stable(self):
        article = {
            "originalTitle": "稳 定 标 题",
            "originalSummary": "摘要",
            "sourceTypeKey": "official",
            "timestamp": 1_700_000_000_000,
        }
        self.assertEqual(shadow.heat_score(article), shadow.heat_score(dict(article)))


if __name__ == "__main__":
    unittest.main()
