"""Poll Google News RSS for keywords and post new hits to a Discord webhook."""

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

KEYWORDS_FILE = "keywords.json"
SEEN_FILE = "seen.json"
RSS_BASE = "https://news.google.com/rss/search"
WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")
MAX_SEEN = 3000
REQUEST_TIMEOUT = 20
EMBED_COLOR = 0x1DB954
USER_AGENT = "Mozilla/5.0 (compatible; ubaid-footy-alerts/1.0)"

OG_IMAGE_RE = re.compile(
    r'<meta[^>]+(?:property|name)=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']'
    r'|<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']og:image["\']',
    re.IGNORECASE,
)


def fetch_articles(query):
    params = {"q": query, "hl": "en-PK", "gl": "PK", "ceid": "PK:en"}
    url = f"{RSS_BASE}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
        data = resp.read()

    root = ET.fromstring(data)
    articles = []
    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub_date = (item.findtext("pubDate") or "").strip()
        source_el = item.find("source")
        source = (source_el.text or "").strip() if source_el is not None else ""
        if title and link:
            articles.append(
                {"title": title, "link": link, "source": source, "pub_date": pub_date}
            )
    return articles


def find_preview_image(article_url):
    """Best-effort scrape of the article's og:image. Any failure just means no thumbnail."""
    try:
        req = urllib.request.Request(article_url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=10) as resp:
            html = resp.read(300_000).decode("utf-8", errors="replace")
        match = OG_IMAGE_RE.search(html)
        if match:
            return match.group(1) or match.group(2)
    except Exception:
        pass
    return None


def build_embed(keyword, article):
    embed = {
        "title": article["title"][:256],
        "url": article["link"],
        "color": EMBED_COLOR,
        "author": {"name": keyword.strip('"')},
    }
    if article["source"]:
        embed["footer"] = {"text": article["source"]}
    if article["pub_date"]:
        try:
            embed["timestamp"] = parsedate_to_datetime(article["pub_date"]).isoformat()
        except (TypeError, ValueError):
            pass

    image_url = find_preview_image(article["link"])
    if image_url:
        embed["image"] = {"url": image_url}

    return embed


def post_to_discord(keyword, article):
    if not WEBHOOK_URL:
        raise RuntimeError("DISCORD_WEBHOOK_URL is not set")

    payload = json.dumps({"embeds": [build_embed(keyword, article)]}).encode("utf-8")
    req = urllib.request.Request(
        WEBHOOK_URL,
        data=payload,
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            resp.read()
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {e.code}: {body}") from None


def load_seen():
    if not os.path.exists(SEEN_FILE):
        return {}
    with open(SEEN_FILE, "r", encoding="utf-8") as f:
        raw = json.load(f)
    # Back-compat: older format was a plain list of links.
    if isinstance(raw, list):
        now = datetime.now(timezone.utc).isoformat()
        return {link: now for link in raw}
    return raw


def save_seen(seen):
    if len(seen) > MAX_SEEN:
        oldest_first = sorted(seen.items(), key=lambda kv: kv[1])
        seen = dict(oldest_first[-MAX_SEEN:])
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(seen, f, indent=2, sort_keys=True)


def main():
    with open(KEYWORDS_FILE, "r", encoding="utf-8") as f:
        keywords = json.load(f)

    seen = load_seen()
    posted = 0

    for keyword in keywords:
        try:
            articles = fetch_articles(keyword)
        except (urllib.error.URLError, ET.ParseError) as e:
            print(f"[warn] fetch failed for {keyword!r}: {e}")
            continue

        for article in articles:
            link = article["link"]
            if link in seen:
                continue

            try:
                post_to_discord(keyword, article)
            except (urllib.error.URLError, RuntimeError) as e:
                print(f"[error] discord post failed for {article['title']!r}: {e}")
                continue

            seen[link] = datetime.now(timezone.utc).isoformat()
            posted += 1
            print(f"[posted] {keyword}: {article['title']}")
            time.sleep(1)  # stay well under Discord's rate limit

    save_seen(seen)
    print(f"Done. {posted} new article(s) posted.")


if __name__ == "__main__":
    main()
