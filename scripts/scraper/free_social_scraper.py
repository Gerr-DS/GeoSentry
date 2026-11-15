"""
GeoSentry - Public Social Media Trend Scraper (Tanpa API Key)
Mengambil konten populer/trending yang diterbitkan dalam 24 jam terakhir dari:
  Mastodon trending API, tren asli Bluesky, Reddit new + top/day,
  tren X Indonesia, dan hasil indeks pencarian publik untuk URL posting X,
  Instagram, dan video TikTok.

Tidak memakai API key berbayar. Rate limit dijaga wajar (sleep antar request),
semua kegagalan per sumber ditangani lembut (skip + log) agar pipeline tetap jalan.
Data -> klasifikasi AI (predict_threat) -> geocoding (extract_location) -> PostgreSQL `social_threats`.
"""

import os
import sys
import json
import calendar
import time
import socket
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

# Paksa resolusi IPv4 untuk menghindari delay SYN_SENT pada jaringan yang tidak memiliki rute IPv6
orig_getaddrinfo = socket.getaddrinfo
def getaddrinfo_ipv4(host, port, family=0, type=0, proto=0, flags=0):
    return orig_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)
socket.getaddrinfo = getaddrinfo_ipv4
socket.setdefaulttimeout(6)

import argparse
from datetime import datetime, timezone, timedelta
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

import requests
import feedparser
import yt_dlp
from bs4 import BeautifulSoup
from dotenv import load_dotenv
import psycopg

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(dotenv_path=os.path.join(BASE_DIR, ".env"))

DATABASE_URL = os.getenv("DATABASE_URL")

if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from scripts.nlp.predict import predict_threat
from scripts.gazetteer.geocode import extract_location
from scripts.tools.verification_gate import verify_social_post, social_verification_metrics

HTTP_HEADERS = {
    "User-Agent": "GeoSentry/1.0 (free social monitor; local research dashboard)",
    "Accept": "application/json",
}
TIMEOUT = 5

# Sumber publik gratis; item lama atau tanpa waktu terbit dikeluarkan dari jendela tren.
TREND_WINDOW = timedelta(hours=24)
TRENDING_TOPICS = []
MASTODON_INSTANCES = ["mastodon.social", "mastodon.kodesumber.com"]
MASTODON_LOCAL_INSTANCES = ["mastodon.kodesumber.com"]  # instance lokal Indonesia (timeline publik)
REDDIT_SUBS = [
    "indonesia", "indonesianews", "duniaberita", "PolitikIndo", "indotech", "finansial",
    "Jakarta", "Bandung", "surabaya", "bali", "jogja", "Medan", "Makassar",
    "Palembang", "Balikpapan", "Sulawesi", "sumatra", "westpapua",
    "news", "worldnews", "popular", "technology", "geopolitics",
]

# Public search-engine index fallback. It yields public platform URLs only;
# there is no dependency on RSSHub or private browser sessions.
SOCIAL_SEARCH_DOMAINS = {
    "twitter": ("x.com", "twitter.com"),
    "instagram": ("instagram.com",),
    "tiktok": ("tiktok.com",),
}
SOURCE_FETCH_STATUS = {}


def is_recent(value):
    """True only for a known timestamp within the rolling last 24 hours."""
    if not isinstance(value, datetime):
        return False
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    value = value.astimezone(timezone.utc)
    return now - TREND_WINDOW <= value <= now + timedelta(minutes=5)


def keep_recent(items):
    return [item for item in items if is_recent(item.get("published_at"))]


def get_x_trending_topics(limit=12):
    """Read Indonesia's public 24-hour X trend list; no fixed claim-check terms."""
    url = "https://trends24.in/indonesia/"
    try:
        response = requests.get(url, headers={**HTTP_HEADERS, "Accept": "text/html"}, timeout=8)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        topics = []
        for anchor in soup.find_all("a", href=True):
            href = anchor["href"].lower()
            topic = " ".join(anchor.get_text(" ", strip=True).split())
            if not any(domain in href for domain in ("twitter.com", "x.com")) or not topic:
                continue
            if topic.lower() in {t.lower() for t in topics}:
                continue
            if len(topic) < 3 or len(topic) > 100:
                continue
            topics.append(topic)
            if len(topics) >= limit:
                break
        if topics:
            SOURCE_FETCH_STATUS["twitter_trends"] = {"status": "berhasil", "jumlah": len(topics), "window": "24 jam"}
            return topics
        SOURCE_FETCH_STATUS["twitter_trends"] = {"status": "kosong", "jumlah": 0, "alasan": "daftar tren tidak ditemukan"}
    except Exception as exc:
        SOURCE_FETCH_STATUS["twitter_trends"] = {"status": "tidak_tersedia", "jumlah": 0, "alasan": f"{type(exc).__name__}: {exc}"}
    return []


def add_trending_topics(items, limit=15):
    """Derive search terms from actual high-engagement public posts, not a static list."""
    global TRENDING_TOPICS
    ranked = []
    seen = {topic.casefold() for topic in TRENDING_TOPICS}
    for item in items:
        text = item.get("teks", "")
        meta = item.get("meta", {})
        score = int(meta.get("score", 0) or 0) + int(meta.get("reblogs", 0) or 0) + int(meta.get("favourites", 0) or 0)
        candidates = re.findall(r"#[\w\u0080-\uffff]+", text)
        if not candidates:
            candidates = [" ".join(text.split()[:5]).strip(".,!?;:")]
        for candidate in candidates:
            candidate = candidate.strip()
            if len(candidate) < 3 or candidate.casefold() in seen:
                continue
            seen.add(candidate.casefold())
            ranked.append((score, candidate))
    for _, topic in sorted(ranked, reverse=True):
        TRENDING_TOPICS.append(topic)
        if len(TRENDING_TOPICS) >= limit:
            break
    return TRENDING_TOPICS[:limit]


def social_post_url(source, url):
    """Allow only direct post URLs from the requested social platform."""
    parsed = urlparse(url or "")
    host = (parsed.hostname or "").lower().removeprefix("www.")
    path = parsed.path or ""
    allowed_domains = SOCIAL_SEARCH_DOMAINS.get(source, ())
    if not any(host == domain or host.endswith("." + domain) for domain in allowed_domains):
        return False
    if source == "twitter":
        return bool(re.search(r"/(?:[^/]+/)?status/\d+", path))
    if source == "instagram":
        return bool(re.search(r"/(?:p|reel|reels)/[^/]+", path, re.I))
    if source == "tiktok":
        return bool(re.search(r"/@[^/]+/video/\d+", path, re.I))
    return False


def unpack_search_result_url(url):
    """Resolve the redirect URL used by some search-result feeds."""
    parsed = urlparse(url or "")
    for key in ("url", "u", "target"):
        value = parse_qs(parsed.query).get(key, [""])[0]
        if value.startswith(("http://", "https://")):
            return unquote(value)
    return url


def search_result_published_at(entry):
    published = entry.get("published_parsed") or entry.get("updated_parsed")
    if not published:
        return None
    return datetime.fromtimestamp(calendar.timegm(published), tz=timezone.utc)


def extract_direct_social_post(source, url, fallback_text, fallback_title, fallback_time, topic):
    """Read metadata from a direct post URL without downloading its media."""
    text = (fallback_text or fallback_title or "").strip()
    published_at = fallback_time
    canonical_url = url
    metadata = {"trend": topic, "method": "public search index"}
    # TikTok oEmbed can provide a title even when the video extractor is
    # temporarily blocked. The URL must already be a direct video URL.
    if source == "tiktok" and len(text) < 25:
        try:
            response = requests.get(
                "https://www.tiktok.com/oembed",
                params={"url": url},
                headers=HTTP_HEADERS,
                timeout=TIMEOUT,
            )
            response.raise_for_status()
            embed = response.json()
            text = (embed.get("title") or text).strip()
            metadata.update({
                "method": "public search index + TikTok oEmbed",
                "author": embed.get("author_name") or "",
            })
        except Exception as exc:
            metadata["oembed_error"] = f"{type(exc).__name__}: {exc}"
    # Use yt-dlp only for direct TikTok videos, where it may recover the
    # original upload time. Avoid its fragile X/Instagram extractors.
    if source == "tiktok" and not published_at:
        try:
            options = {
                "quiet": True,
                "no_warnings": True,
                "skip_download": True,
                "socket_timeout": 5,
                "http_headers": HTTP_HEADERS,
            }
            with yt_dlp.YoutubeDL(options) as ydl:
                info = ydl.extract_info(url, download=False)
            if info:
                description = (info.get("description") or "").strip()
                title = (info.get("title") or "").strip()
                text = description or title or text
                canonical_url = info.get("webpage_url") or url
                timestamp = info.get("timestamp")
                if timestamp:
                    published_at = datetime.fromtimestamp(timestamp, tz=timezone.utc)
                elif info.get("upload_date"):
                    try:
                        published_at = datetime.strptime(info["upload_date"], "%Y%m%d").replace(tzinfo=timezone.utc)
                    except ValueError:
                        pass
                metadata.update({
                    "method": "indeks pencarian + metadata video TikTok langsung",
                    "author": info.get("uploader") or info.get("channel") or "",
                })
        except Exception as exc:
            metadata["metadata_error"] = f"{type(exc).__name__}: {exc}"

    if not text or not social_post_url(source, canonical_url):
        return None
    return {
        "sumber": source,
        "teks": text[:5000],
        "url": canonical_url,
        "published_at": published_at,
        "meta": metadata,
    }


def fetch_public_social_search(source, topics, limit):
    """Find direct public post URLs through Bing's search index, not RSSHub."""
    domains = SOCIAL_SEARCH_DOMAINS[source]
    site_clause = " OR ".join(f"site:{domain}" for domain in domains)
    found, seen = [], set()
    last_error = "hasil pencarian tidak memuat postingan publik yang cocok"
    successful_queries = 0
    candidate_count = 0

    topic_limit = min(5, max(1, limit))
    candidate_limit = min(12, max(6, limit)) if source == "tiktok" else max(limit, 20)
    for topic in topics[:topic_limit]:
        query = f"{site_clause} {topic}"
        feed_url = f"https://www.bing.com/search?format=rss&q={quote_plus(query)}"
        try:
            response = requests.get(
                feed_url,
                headers={**HTTP_HEADERS, "Accept": "application/rss+xml, application/xml, text/xml"},
                timeout=8,
            )
            response.raise_for_status()
            entries = feedparser.parse(response.content).entries
            successful_queries += 1
            if not entries:
                last_error = "indeks pencarian tidak memberi hasil untuk topik tren"
                continue
            for entry in entries[:12]:
                post_url = unpack_search_result_url(entry.get("link", "").strip())
                if not social_post_url(source, post_url) or post_url in seen:
                    continue
                seen.add(post_url)
                candidate_count += 1
                fallback_title = strip_html(entry.get("title", ""))
                fallback_text = strip_html(entry.get("summary", entry.get("description", "")))
                post = extract_direct_social_post(
                    source,
                    post_url,
                    fallback_text,
                    fallback_title,
                    search_result_published_at(entry),
                    topic,
                )
                if post and is_recent(post.get("published_at")):
                    found.append(post)
                    if len(found) >= limit:
                        break
                elif post and not post.get("published_at"):
                    last_error = "postingan ditemukan, tetapi waktu terbitnya tidak dapat dibaca"
                if candidate_count >= candidate_limit:
                    break
            if len(found) >= limit:
                break
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"

    status = "berhasil" if found else ("kosong" if successful_queries else "tidak_tersedia")
    SOURCE_FETCH_STATUS[source] = {
        "status": status,
        "jumlah": len(found),
        "metode": "indeks pencarian publik + pembacaan URL postingan asli; tanpa RSSHub",
        "window": "24 jam",
        **({} if found else {"alasan": last_error}),
    }
    if not found:
        print(f"[!] {source}: {last_error}; kandidat langsung={candidate_count}", file=sys.stderr)
    return found


def fetch_x_public(limit):
    """Find recent direct X posts for Indonesia's public trending topics."""
    topics = get_x_trending_topics()
    if topics:
        TRENDING_TOPICS[:] = topics
    else:
        topics = TRENDING_TOPICS[:]
    if not topics:
        SOURCE_FETCH_STATUS["twitter"] = {
            "status": "tidak_tersedia",
            "jumlah": 0,
            "alasan": "tidak ada topik tren publik untuk dicari",
        }
        return []
    return fetch_public_social_search("twitter", topics, limit)


def fetch_tiktok_public(limit):
    """Find direct TikTok video URLs for current cross-platform trend topics."""
    topics = TRENDING_TOPICS[:min(8, limit)]
    if not topics:
        SOURCE_FETCH_STATUS["tiktok"] = {"status": "kosong", "jumlah": 0, "alasan": "belum ada topik tren untuk dicari"}
        return []
    return fetch_public_social_search("tiktok", topics, limit)


def fetch_instagram_public(limit):
    """Find recent direct Instagram posts for current trend topics."""
    topics = TRENDING_TOPICS[:min(8, limit)]
    if not topics:
        SOURCE_FETCH_STATUS["instagram"] = {"status": "kosong", "jumlah": 0, "alasan": "belum ada topik tren untuk dicari"}
        return []
    return fetch_public_social_search("instagram", topics, limit)


def strip_html(raw):
    """Membersihkan HTML dari konten Mastodon."""
    if not raw:
        return ""
    return BeautifulSoup(raw, "html.parser").get_text(" ", strip=True)


def parse_iso(value):
    """Parse timestamp ISO8601 (mastodon/bluesky) ke datetime aware UTC."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def fetch_mastodon(limit):
    """Ambil status yang sedang trending dari endpoint publik Mastodon."""
    items = []
    for instance in MASTODON_INSTANCES:
        url = f"https://{instance}/api/v1/trends/statuses?limit={min(40, max(limit, 10))}"
        try:
            resp = requests.get(url, headers=HTTP_HEADERS, timeout=TIMEOUT)
            if resp.status_code != 200:
                continue
            for status in resp.json():
                if status.get("visibility") != "public":
                    continue
                text = strip_html(status.get("content", ""))
                if not text.strip():
                    continue
                card_url = (status.get("card") or {}).get("url")
                items.append({"sumber": "mastodon", "teks": text.strip(), "url": status.get("url", ""),
                    "published_at": parse_iso(status.get("created_at")),
                    "meta": {"instance": instance, "reblogs": status.get("reblogs_count", 0), "favourites": status.get("favourites_count", 0), "external_url": card_url}})
        except Exception as exc:
            print(f"[!] Gagal mengambil Mastodon trends dari {instance}: {exc}", file=sys.stderr)
    recent = keep_recent(items)[:limit]
    local_recent = fetch_mastodon_public_local(limit)
    combined = []
    seen = set()
    for item in recent + local_recent:
        key = item.get("url") or item.get("teks", "")[:200]
        if not key or key in seen:
            continue
        seen.add(key)
        combined.append(item)
    add_trending_topics(combined)
    SOURCE_FETCH_STATUS["mastodon"] = {
        "status": "berhasil" if combined else "kosong",
        "jumlah": len(combined),
        "trending": len(recent),
        "timeline_lokal": len(local_recent),
        "window": "24 jam",
    }
    return combined[:max(1, limit) * 2]


def fetch_mastodon_public_local(limit):
    """Mengambil timeline publik lokal instance Indonesia (Bahasa Indonesia)."""
    items = []
    for instance in MASTODON_LOCAL_INSTANCES:
        url = f"https://{instance}/api/v1/timelines/public?local=true&limit={limit}"
        try:
            resp = requests.get(url, headers=HTTP_HEADERS, timeout=TIMEOUT)
            if resp.status_code != 200:
                print(f"[!] Mastodon {instance}/timelines/public -> HTTP {resp.status_code}", file=sys.stderr)
                continue
            for status in resp.json():
                if status.get("visibility") != "public":
                    continue
                lang = (status.get("language") or "").lower()
                if lang and lang not in ("id", "ms"):
                    continue
                text = strip_html(status.get("content", ""))
                if not text.strip():
                    continue
                items.append({
                    "sumber": "mastodon",
                    "teks": text.strip(),
                    "url": status.get("url", ""),
                    "published_at": parse_iso(status.get("created_at")),
                    "meta": {
                        "instance": instance,
                        "timeline": "local",
                        "reblogs": status.get("reblogs_count", 0),
                        "favourites": status.get("favourites_count", 0),
                        "external_url": (status.get("card") or {}).get("url"),
                    },
                })
        except Exception as exc:
            print(f"[!] Gagal mengambil Mastodon {instance}/timelines/public: {exc}", file=sys.stderr)
        time.sleep(0.35)
    recent = keep_recent(items)
    add_trending_topics(recent)
    return recent


def fetch_bluesky(limit):
    """Get Bluesky's own trending topics, then search recent matching posts."""
    items, seen = [], set()
    since = (datetime.now(timezone.utc) - TREND_WINDOW).isoformat(timespec="seconds").replace("+00:00", "Z")
    topic_names = []
    trend_error = None

    try:
        response = requests.get(
            "https://public.api.bsky.app/xrpc/app.bsky.unspecced.getTrends",
            params={"limit": 25}, headers=HTTP_HEADERS, timeout=TIMEOUT,
        )
        response.raise_for_status()
        for trend in response.json().get("trends", []):
            topic = (trend.get("topic") or trend.get("displayName") or "").strip()
            if topic and topic.casefold() not in {name.casefold() for name in topic_names}:
                topic_names.append(topic)
        SOURCE_FETCH_STATUS["bluesky_trends"] = {
            "status": "berhasil" if topic_names else "kosong",
            "jumlah": len(topic_names),
            "metode": "endpoint tren Bluesky",
        }
    except Exception as exc:
        trend_error = f"{type(exc).__name__}: {exc}"
        SOURCE_FETCH_STATUS["bluesky_trends"] = {
            "status": "tidak_tersedia", "jumlah": 0, "alasan": trend_error,
        }

    # Network trends are used first; other live topics are only a fallback.
    if topic_names:
        existing = {topic.casefold() for topic in TRENDING_TOPICS}
        TRENDING_TOPICS.extend(topic for topic in topic_names if topic.casefold() not in existing)
    else:
        topic_names = TRENDING_TOPICS[:]

    if not topic_names:
        SOURCE_FETCH_STATUS["bluesky"] = {
            "status": "tidak_tersedia" if trend_error else "kosong",
            "jumlah": 0,
            "alasan": trend_error or "Bluesky tidak mengembalikan topik tren",
        }
        return []

    query_errors = []
    per_query = min(30, max(10, limit))
    for query in topic_names[:min(12, max(1, limit))]:
        params = {"q": query, "sort": "top", "since": since, "lang": "id", "limit": per_query}
        try:
            resp = requests.get(
                "https://public.api.bsky.app/xrpc/app.bsky.feed.searchPosts",
                params=params, headers=HTTP_HEADERS, timeout=TIMEOUT,
            )
            resp.raise_for_status()
            posts = resp.json().get("posts", [])
            # If a global trend has no Indonesian matches, retry it without
            # the language filter instead of silently dropping it.
            if not posts:
                fallback_params = {key: value for key, value in params.items() if key != "lang"}
                fallback = requests.get(
                    "https://public.api.bsky.app/xrpc/app.bsky.feed.searchPosts",
                    params=fallback_params, headers=HTTP_HEADERS, timeout=TIMEOUT,
                )
                fallback.raise_for_status()
                posts = fallback.json().get("posts", [])

            for post in posts:
                record = post.get("record", {})
                text = (record.get("text") or "").strip()
                if not text:
                    continue
                author = post.get("author", {})
                handle = author.get("handle", "")
                uri = post.get("uri", "")
                rkey = uri.rsplit("/", 1)[-1] if "/" in uri else ""
                post_url = f"https://bsky.app/profile/{handle}/post/{rkey}" if handle and rkey else uri
                if not post_url or post_url in seen:
                    continue
                seen.add(post_url)
                embed = post.get("embed") or record.get("embed") or {}
                external_url = (embed.get("external") or {}).get("uri")
                items.append({
                    "sumber": "bluesky", "teks": text, "url": post_url,
                    "published_at": parse_iso(record.get("createdAt") or post.get("indexedAt")),
                    "meta": {"query": query, "handle": handle, "external_url": external_url},
                })
        except Exception as exc:
            query_errors.append(f"{query}: {type(exc).__name__}: {exc}")
        time.sleep(0.15)

    recent = keep_recent(items)[:limit]
    add_trending_topics(recent)
    used_native_trends = bool(SOURCE_FETCH_STATUS.get("bluesky_trends", {}).get("jumlah"))
    SOURCE_FETCH_STATUS["bluesky"] = {
        "status": "berhasil" if recent else ("kosong" if not query_errors else "tidak_tersedia"),
        "jumlah": len(recent),
        "topik_tren_bluesky": len(topic_names) if used_native_trends else 0,
        "metode": (
            "tren jaringan Bluesky + pencarian posting populer 24 jam"
            if used_native_trends else "topik tren lintas platform sebagai cadangan"
        ),
        **({} if recent else {"alasan": query_errors[0] if query_errors else "tidak ada posting cocok dalam 24 jam"}),
    }
    return recent


def fetch_reddit(limit):
    """Read new and top/day posts from Indonesian and broad topic communities."""
    headers = {
        **HTTP_HEADERS,
        "User-Agent": "Mozilla/5.0 (compatible; GeoSentry public news monitor/1.0)",
    }

    def fetch_subreddit(subreddit):
        posts, errors = [], []
        seen_posts = set()
        endpoints = (
            f"https://www.reddit.com/r/{subreddit}/new.json?limit={min(limit, 50)}",
            f"https://www.reddit.com/r/{subreddit}/top.json?t=day&limit={min(limit, 50)}",
        )
        for url in endpoints:
            try:
                resp = requests.get(url, headers=headers, timeout=TIMEOUT)
                if resp.status_code != 200:
                    errors.append(f"r/{subreddit} HTTP {resp.status_code}")
                    continue
                for child in resp.json().get("data", {}).get("children", []):
                    data = child.get("data", {})
                    title = (data.get("title") or "").strip()
                    selftext = (data.get("selftext") or "").strip()
                    if selftext in ("[removed]", "[deleted]"):
                        selftext = ""
                    text = f"{title}. {selftext}".strip() if selftext else title
                    permalink = data.get("permalink", "")
                    if not text or not permalink or permalink in seen_posts:
                        continue
                    seen_posts.add(permalink)
                    posts.append({
                        "sumber": "reddit",
                        "teks": text,
                        "url": f"https://www.reddit.com{permalink}",
                        "published_at": datetime.fromtimestamp(data.get("created_utc", 0), tz=timezone.utc),
                        "meta": {
                            "score": data.get("score", 0),
                            "subreddit": data.get("subreddit", subreddit),
                            "external_url": data.get("url"),
                        },
                    })
            except Exception as exc:
                errors.append(f"r/{subreddit} {type(exc).__name__}: {exc}")
        return posts, errors

    items, errors = [], []
    # Fetching communities concurrently keeps the wider coverage from making
    # the hourly sync wait through dozens of serial timeouts.
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(fetch_subreddit, subreddit): subreddit for subreddit in REDDIT_SUBS}
        for future in as_completed(futures):
            posts, subreddit_errors = future.result()
            items.extend(posts)
            errors.extend(subreddit_errors)

    recent = keep_recent(items)
    add_trending_topics(recent)
    SOURCE_FETCH_STATUS["reddit"] = {
        "status": "berhasil" if recent else ("kosong" if not errors else "tidak_tersedia"),
        "jumlah": len(recent),
        "komunitas_diperiksa": len(REDDIT_SUBS),
        "komunitas": REDDIT_SUBS,
        "metode": "JSON publik Reddit: new + top harian, jendela 24 jam",
        **({} if recent else {"alasan": errors[0] if errors else "tidak ada posting dalam 24 jam"}),
    }
    if errors:
        print(f"[!] Reddit: {len(errors)} permintaan gagal; contoh: {errors[0]}", file=sys.stderr)
    return recent[:limit]


def sync_free_social_to_db(source_filter="all", limit=20):
    """
    Menjalankan penarikan media sosial gratis, klasifikasi AI, geocoding,
    dan penyimpanan ke PostgreSQL dengan dedupe URL/teks.
    """
    print(f"\n[+] [{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Sinkronisasi Media Sosial Gratis (tanpa API key)...")

    SOURCE_FETCH_STATUS.clear()
    TRENDING_TOPICS[:] = get_x_trending_topics()
    fetchers = {
        "mastodon": fetch_mastodon,
        "bluesky": fetch_bluesky,
        "reddit": fetch_reddit,
        "twitter": fetch_x_public,
        "tiktok": fetch_tiktok_public,
        "instagram": fetch_instagram_public,
    }
    selected = list(fetchers.keys()) if source_filter == "all" else [source_filter]

    # Discover broad trends first, then use the resulting topics to search other
    # public services. These are live terms, not a fixed fact-check keyword list.
    seed_results = {}
    if source_filter == "all":
        seed_results["mastodon"] = fetch_mastodon(limit)
        seed_results["reddit"] = fetch_reddit(limit)
        add_trending_topics(seed_results["mastodon"] + seed_results["reddit"])

    fetched = []
    by_source = {}
    for name in selected:
        try:
            if name in seed_results:
                results = seed_results[name]
            elif name == "mastodon":
                results = fetch_mastodon(limit)
            else:
                results = fetchers[name](limit)
        except Exception as exc:
            print(f"[!] Sumber {name} gagal: {exc}", file=sys.stderr)
            results = []
        by_source[name] = len(results)
        fetched.extend(results)
        print(f"[i] {name}: {len(results)} item")

    # Dedupe dalam satu batch
    seen = set()
    unique = []
    for item in fetched:
        key = item.get("url") or item.get("teks", "")[:200]
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    print(f"[i] Total fetched {len(fetched)} -> {len(unique)} item unik.")

    inserted_count = 0
    updated_count = 0
    withheld_count = 0
    accepted_by_source = {name: 0 for name in selected}
    accepted_by_label = {name: 0 for name in ("hoaks", "fakta", "negatif")}
    withheld_by_source = {name: 0 for name in selected}
    new_items = []
    prepared_items = []

    # Verify before opening a database transaction: portal searches can take
    # several seconds, so keep the DB connection free while network requests run.
    for art in unique:
        text = art["teks"]
        url = art["url"]
        verification = verify_social_post(
            art["sumber"], text, url, original_content_fetched=True,
            referenced_urls=(art.get("meta") or {}).get("external_url"),
        )
        if not verification:
            withheld_count += 1
            withheld_by_source[art["sumber"]] = withheld_by_source.get(art["sumber"], 0) + 1
            continue
        accepted_by_source[art["sumber"]] = accepted_by_source.get(art["sumber"], 0) + 1
        accepted_by_label[verification["label"]] = accepted_by_label.get(verification["label"], 0) + 1
        refs = [{"sumber": art["sumber"], "url": url, "judul": text[:70]}]
        refs.extend(verification.get("referensi", []))
        prepared_items.append((art, verification, refs))

    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            for art, verification, refs in prepared_items:
                text = art["teks"]
                url = art["url"]
                published_at = art["published_at"]
                cur.execute(
                    "SELECT id FROM social_threats WHERE url = %s OR teks = %s LIMIT 1;",
                    (url, text),
                )
                existing = cur.fetchone()

                if existing:
                    cur.execute(
                        """UPDATE social_threats
                           SET status_tayang = TRUE, dibuat_pada = %s, label = %s, probabilitas = %s,
                               referensi_urls = %s::jsonb,
                               verifikasi_status = %s, verifikasi_dasar = %s, verifikasi_pada = NOW()
                           WHERE id = %s;""",
                        (published_at, verification["label"], verification["probabilitas"], json.dumps(refs), verification["status"], verification["basis"], existing[0]),
                    )
                    updated_count += 1
                    continue

                loc = extract_location(text, default_if_none=True)
                if not loc:
                    loc = {"nama": "Nasional (Indonesia)", "provinsi": "Indonesia", "lat": -2.2, "lon": 118.0}

                cur.execute(
                    """
                    INSERT INTO social_threats
                    (sumber, teks, label, probabilitas, geom, lokasi_nama, url, referensi_urls,
                     status_tayang, verifikasi_status, verifikasi_dasar, verifikasi_pada, dikoreksi, dibuat_pada)
                    VALUES
                    (%s, %s, %s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326), %s, %s, %s::jsonb,
                     TRUE, %s, %s, NOW(), FALSE, %s)
                    RETURNING id;
                    """,
                    (
                        art["sumber"],
                        text,
                        verification["label"],
                        verification["probabilitas"],
                        loc["lon"],
                        loc["lat"],
                        loc["nama"],
                        url,
                        json.dumps(refs),
                        verification["status"],
                        verification["basis"],
                        published_at,
                    ),
                )
                new_id = cur.fetchone()[0]
                inserted_count += 1
                new_items.append({
                    "id": new_id,
                    "sumber": art["sumber"],
                    "teks": text,
                    "label": verification["label"],
                    "probabilitas": verification["probabilitas"],
                    "geom": {"type": "Point", "coordinates": [loc["lon"], loc["lat"]]},
                    "lokasi_nama": loc["nama"],
                    "url": url,
                    "referensi_urls": refs,
                    "verifikasi_status": verification["status"],
                    "verifikasi_dasar": verification["basis"],
                    "dibuat_pada": published_at.isoformat() if hasattr(published_at, "isoformat") else str(published_at),
                })

            conn.commit()

    result = {
        "status": "success",
        "source_filter": source_filter,
        "fetched": len(fetched),
        "unique": len(unique),
        "by_source": by_source,
        "verified_by_source": accepted_by_source,
        "verified_by_label": accepted_by_label,
        "verification_summary": social_verification_metrics(),
        "withheld_by_source": withheld_by_source,
        "source_status": {
            name: SOURCE_FETCH_STATUS.get(
                name,
                {"status": "berhasil" if by_source.get(name, 0) else "kosong", "jumlah": by_source.get(name, 0)},
            )
            for name in selected
        },
        "inserted": inserted_count,
        "updated": updated_count,
        "withheld": withheld_count,
        "new_items": new_items,
    }
    print(f"[✓] Sinkronisasi media sosial selesai: +{inserted_count} baru, {updated_count} diperbarui.")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="GeoSentry Free Social Media Scraper (no API key)")
    parser.add_argument("--limit", type=int, default=20, help="Jumlah maksimal per query per sumber")
    parser.add_argument("--source", choices=["mastodon", "bluesky", "reddit", "twitter", "tiktok", "instagram", "all"], default="all",
                        help="Batasi ke satu sumber (default: all)")
    args = parser.parse_args()

    result = sync_free_social_to_db(source_filter=args.source, limit=args.limit)
    print(json.dumps(result, indent=2))
