"""
抓財經新聞標題（RSS，不需要註冊、不需要金鑰）。
台股：Yahoo奇摩股市
美股：Yahoo Finance
"""
import requests
import feedparser

import config


def _fetch_feed(url: str, limit: int):
    try:
        resp = requests.get(url, headers={"User-Agent": config.NEWS_USER_AGENT}, timeout=20)
        resp.raise_for_status()
    except requests.RequestException:
        return []

    parsed = feedparser.parse(resp.content)
    items = []
    for entry in parsed.entries[:limit]:
        items.append({
            "title": entry.get("title", "").strip(),
            "link": entry.get("link", ""),
            "published": entry.get("published", entry.get("updated", "")),
        })
    return items


def get_tw_news():
    news = []
    for url in config.TW_NEWS_RSS_FEEDS:
        news.extend(_fetch_feed(url, config.NEWS_ITEMS_PER_FEED))
    return _dedupe(news)


def get_us_news():
    news = []
    for url in config.US_NEWS_RSS_FEEDS:
        news.extend(_fetch_feed(url, config.NEWS_ITEMS_PER_FEED))
    return _dedupe(news)


def _dedupe(news):
    seen = set()
    result = []
    for item in news:
        if item["title"] and item["title"] not in seen:
            seen.add(item["title"])
            result.append(item)
    return result


if __name__ == "__main__":
    tw = get_tw_news()
    us = get_us_news()
    print(f"台股新聞 {len(tw)} 則，美股新聞 {len(us)} 則")
    for item in tw[:3]:
        print("TW:", item["title"])
    for item in us[:3]:
        print("US:", item["title"])
