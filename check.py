"""Poll Google News RSS for keywords and post new hits to a Discord webhook."""

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

NAME_RE = re.compile(r'^"([^"]+)"')
CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

KEYWORDS_FILE = "keywords.json"
SEEN_FILE = "seen.json"
RSS_BASE = "https://news.google.com/rss/search"
WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")
MAX_SEEN = 3000
REQUEST_TIMEOUT = 20
EMBED_COLOR = 0x1DB954
USER_AGENT = "Mozilla/5.0 (compatible; ubaid-footy-alerts/1.0)"
# Some publishers (AP of Pakistan, government press releases) ship
# headlines that list out several officials by name and run very long.
# Cap well under Discord's limits so nothing ever gets rejected for length.
TITLE_LIMIT = 300
# Google News RSS search returns whatever it considers most *relevant* to
# the query, not strictly the most recent - a years-old article can rotate
# back into a query's results and look "new" since we've never seen its
# link before. Anything older than this is ignored rather than posted.
MAX_ARTICLE_AGE = timedelta(days=3)


def clean_text(text, limit):
    text = CONTROL_CHARS_RE.sub("", text)
    text = " ".join(text.split())
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


def parse_pub_date(article):
    try:
        return parsedate_to_datetime(article["pub_date"])
    except (TypeError, ValueError):
        return None


def fetch_articles(query):
    params = {"q": query, "hl": "en-PK", "gl": "PK", "ceid": "PK:en"}
    url = f"{RSS_BASE}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
        data = resp.read()

    root = ET.fromstring(data)
    articles = []
    for item in root.findall(".//item"):
        title = clean_text((item.findtext("title") or "").strip(), TITLE_LIMIT)
        link = (item.findtext("link") or "").strip()
        pub_date = (item.findtext("pubDate") or "").strip()
        source_el = item.find("source")
        source = (source_el.text or "").strip() if source_el is not None else ""
        if title and link:
            articles.append(
                {"title": title, "link": link, "source": source, "pub_date": pub_date}
            )
    return articles


def display_name(keyword):
    match = NAME_RE.match(keyword)
    return match.group(1) if match else keyword


def build_embed(keyword, article):
    # No thumbnail: Google News RSS links go to a JS-redirect interstitial
    # page, not the publisher's page, so there's no real og:image to read.
    embed = {
        "title": article["title"][:256],
        "url": article["link"],
        "color": EMBED_COLOR,
        "author": {"name": display_name(keyword)[:256]},
    }
    if article["source"]:
        embed["footer"] = {"text": article["source"][:2048]}
    if article["pub_date"]:
        try:
            embed["timestamp"] = parsedate_to_datetime(article["pub_date"]).isoformat()
        except (TypeError, ValueError):
            pass
    return embed


MAX_RATE_LIMIT_RETRIES = 5


def send_webhook(payload_dict):
    payload = json.dumps(payload_dict).encode("utf-8")
    req = urllib.request.Request(
        WEBHOOK_URL,
        data=payload,
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
        method="POST",
    )

    for attempt in range(MAX_RATE_LIMIT_RETRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                resp.read()
            return
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            if e.code == 429 and attempt < MAX_RATE_LIMIT_RETRIES:
                try:
                    retry_after = json.loads(body).get("retry_after", 1)
                except json.JSONDecodeError:
                    retry_after = 1
                time.sleep(float(retry_after) + 0.25)
                continue
            raise RuntimeError(f"HTTP {e.code}: {body}") from None


class UnpostableArticle(Exception):
    """Every fallback failed for reasons unrelated to rate limiting - give up
    on this one instead of retrying it forever."""


def post_to_discord(keyword, article):
    if not WEBHOOK_URL:
        raise RuntimeError("DISCORD_WEBHOOK_URL is not set")

    mention = {"parse": ["everyone"]}
    attempts = [
        {
            "content": "@everyone",
            "embeds": [build_embed(keyword, article)],
            "allowed_mentions": mention,
        },
        {
            "content": f"@everyone\n**{display_name(keyword)}**\n{article['title']}\n{article['link']}",
            "allowed_mentions": mention,
        },
        # Last resort: some Google News links for heavily-clustered stories
        # are themselves over a thousand characters, which can blow past
        # Discord's 2000-char content limit even with the title dropped.
        {"content": f"@everyone {article['link']}", "allowed_mentions": mention},
    ]

    last_error = None
    for i, payload in enumerate(attempts):
        try:
            send_webhook(payload)
            if i > 0:
                print(f"[warn] posted via fallback #{i} after earlier rejection: {last_error}")
            return
        except RuntimeError as e:
            if "HTTP 429" in str(e):
                raise
            last_error = e

    raise UnpostableArticle(str(last_error))


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


def article_sort_key(candidate):
    _, article = candidate
    return parse_pub_date(article) or datetime.min.replace(tzinfo=timezone.utc)


def main():
    with open(KEYWORDS_FILE, "r", encoding="utf-8") as f:
        keywords = json.load(f)

    seen = load_seen()

    # Gather every new match across all keywords first (deduping links seen
    # more than once in this batch too) so we can post oldest-to-newest
    # instead of one keyword's whole result set at a time.
    candidates = []
    links_in_batch = set()
    for keyword in keywords:
        try:
            articles = fetch_articles(keyword)
        except (urllib.error.URLError, ET.ParseError) as e:
            print(f"[warn] fetch failed for {keyword!r}: {e}")
            continue

        for article in articles:
            link = article["link"]
            if link in seen or link in links_in_batch:
                continue
            links_in_batch.add(link)
            candidates.append((keyword, article))

    candidates.sort(key=article_sort_key)

    posted = 0
    skipped = 0
    stale = 0
    now = datetime.now(timezone.utc)
    fresh_candidates = []
    for keyword, article in candidates:
        pub_date = parse_pub_date(article)
        if pub_date is not None and (now - pub_date) > MAX_ARTICLE_AGE:
            print(f"[stale] skipping ({pub_date.date()}) {article['title']!r}")
            seen[article["link"]] = now.isoformat()
            stale += 1
            continue
        fresh_candidates.append((keyword, article))
    candidates = fresh_candidates

    for keyword, article in candidates:
        try:
            post_to_discord(keyword, article)
        except UnpostableArticle as e:
            # Every fallback failed for a reason that will never resolve on
            # its own (e.g. a pathologically long link) - mark it seen so it
            # doesn't get retried and fail again every run forever.
            print(f"[skip] giving up on {article['title']!r}: {e}")
            seen[article["link"]] = datetime.now(timezone.utc).isoformat()
            skipped += 1
            continue
        except (urllib.error.URLError, RuntimeError) as e:
            print(f"[error] discord post failed for {article['title']!r}: {e}")
            continue

        seen[article["link"]] = datetime.now(timezone.utc).isoformat()
        posted += 1
        print(f"[posted] {keyword}: {article['title']}")
        time.sleep(1)  # stay well under Discord's rate limit

    save_seen(seen)
    print(
        f"Done. {posted} new article(s) posted, {stale} stale (skipped), "
        f"{skipped} permanently unpostable."
    )


if __name__ == "__main__":
    main()
