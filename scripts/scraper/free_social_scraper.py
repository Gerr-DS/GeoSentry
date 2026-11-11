"""
GeoSentry - Free Social Media Scraper (Tanpa API Key)
Mengambil data sosial media asli dari sumber publik gratis:
  1. Mastodon  : API publik instance (timelines/tag) - tanpa auth.
  2. Bluesky   : public.api.bsky.app (public API) - tanpa auth.
  3. Reddit    : JSON publik r/indonesia - tanpa auth (User-Agent identitas).

Tidak memakai API key berbayar. Rate limit dijaga wajar (sleep antar request),
semua kegagalan per sumber ditangani lembut (skip + log) agar pipeline tetap jalan.
Data -> klasifikasi AI (predict_threat) -> geocoding (extract_location) -> PostgreSQL `social_threats`.
"""

import os
import sys
import json
import time
import argparse
from datetime import datetime, timezone
from urllib.parse import quote

import requests
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

HTTP_HEADERS = {
    "User-Agent": "GeoSentry/1.0 (free social monitor; local research dashboard)",
    "Accept": "application/json",
}
TIMEOUT = 15

# Sumber gratis tanpa API key
MASTODON_INSTANCES = ["mastodon.social", "mastodon.kodesumber.com"]
MASTODON_LOCAL_INSTANCES = ["mastodon.kodesumber.com"]  # instance lokal Indonesia (timeline publik)
MASTODON_TAGS = [
    "hoaks", "disinformasi", "cekfakta", "turnbackhoax",
    "indonesia", "viral", "berita", "bohong",
]
BLUESKY_QUERIES = [
    "hoaks", "disinformasi", "cekfakta", "turnbackhoax",
    "klaim palsu", "indonesia",
]
REDDIT_SUB = "indonesia"
REDDIT_QUERIES = ["hoaks", "disinformasi", "klaim", "viral", "bohong"]


def strip_html(raw):
    """Membersihkan HTML dari konten Mastodon."""
    if not raw:
        return ""
    return BeautifulSoup(raw, "html.parser").get_text(" ", strip=True)


def parse_iso(value):
    """Parse timestamp ISO8601 (mastodon/bluesky) ke datetime aware UTC."""
    if not value:
        return datetime.now(timezone.utc)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return datetime.now(timezone.utc)


def fetch_mastodon(limit):
    """Mengambil status publik per hashtag dari instance Mastodon publik."""
    items = []
    for instance in MASTODON_INSTANCES:
        for tag in MASTODON_TAGS:
            url = f"https://{instance}/api/v1/timelines/tag/{tag}?limit={limit}"
            try:
                resp = requests.get(url, headers=HTTP_HEADERS, timeout=TIMEOUT)
                if resp.status_code != 200:
                    print(f"[!] Mastodon {instance}/tag/{tag} -> HTTP {resp.status_code}", file=sys.stderr)
                    continue
                for status in resp.json():
                    if status.get("visibility") != "public":
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
                            "tag": tag,
                            "reblogs": status.get("reblogs_count", 0),
                            "favourites": status.get("favourites_count", 0),
                        },
                    })
            except Exception as exc:
                print(f"[!] Gagal mengambil Mastodon {instance}/tag/{tag}: {exc}", file=sys.stderr)
            time.sleep(0.35)
    return items


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
                    },
                })
        except Exception as exc:
            print(f"[!] Gagal mengambil Mastodon {instance}/timelines/public: {exc}", file=sys.stderr)
        time.sleep(0.35)
    return items


def fetch_bluesky(limit):
    """Mengambil postingan publik Bluesky lewat public API (tanpa auth)."""
    items = []
    for query in BLUESKY_QUERIES:
        url = (f"https://public.api.bsky.app/xrpc/app.bsky.feed/searchPosts"
               f"?q={quote(query)}&limit={limit}")
        try:
            resp = requests.get(url, headers=HTTP_HEADERS, timeout=TIMEOUT)
            if resp.status_code in (401, 403):
                print(f"[!] Bluesky diblokir jaringan ini (HTTP {resp.status_code}) -> dilewati.", file=sys.stderr)
                continue
            if resp.status_code != 200:
                print(f"[!] Bluesky '{query}' -> HTTP {resp.status_code}", file=sys.stderr)
                continue
            for post in resp.json().get("posts", []):
                record = post.get("record", {})
                text = (record.get("text") or "").strip()
                if not text:
                    continue
                handle = post.get("author", {}).get("handle", "")
                uri = post.get("uri", "")
                rkey = uri.split("/")[-1] if "/" in uri else ""
                post_url = f"https://bsky.app/profile/{handle}/post/{rkey}" if handle and rkey else uri
                items.append({
                    "sumber": "bluesky",
                    "teks": text,
                    "url": post_url,
                    "published_at": parse_iso(record.get("createdAt")),
                    "meta": {"query": query, "handle": handle},
                })
        except Exception as exc:
            print(f"[!] Gagal mengambil Bluesky '{query}': {exc}", file=sys.stderr)
        time.sleep(0.35)
    return items


def fetch_reddit(limit):
    """Mengambil post publik r/indonesia lewat JSON endpoint (tanpa auth)."""
    items = []
    for query in REDDIT_QUERIES:
        url = (f"https://www.reddit.com/r/{REDDIT_SUB}/search.json"
               f"?q={quote(query)}&restrict_sr=1&sort=new&t=week&limit={limit}")
        try:
            resp = requests.get(url, headers=HTTP_HEADERS, timeout=TIMEOUT)
            if resp.status_code in (401, 403, 429):
                print(f"[!] Reddit diblokir jaringan ini (HTTP {resp.status_code}) -> dilewati.", file=sys.stderr)
                continue
            if resp.status_code != 200:
                print(f"[!] Reddit '{query}' -> HTTP {resp.status_code}", file=sys.stderr)
                continue
            for child in resp.json().get("data", {}).get("children", []):
                data = child.get("data", {})
                title = (data.get("title") or "").strip()
                selftext = (data.get("selftext") or "").strip()
                if selftext in ("[removed]", "[deleted]"):
                    selftext = ""
                text = f"{title}. {selftext}".strip() if selftext else title
                if not text:
                    continue
                permalink = data.get("permalink", "")
                items.append({
                    "sumber": "reddit",
                    "teks": text,
                    "url": f"https://www.reddit.com{permalink}" if permalink else (data.get("url") or ""),
                    "published_at": datetime.fromtimestamp(data.get("created_utc", 0), tz=timezone.utc),
                    "meta": {"query": query, "subreddit": data.get("subreddit", REDDIT_SUB)},
                })
        except Exception as exc:
            print(f"[!] Gagal mengambil Reddit '{query}': {exc}", file=sys.stderr)
        time.sleep(0.5)
    return items


def sync_free_social_to_db(source_filter="all", limit=20):
    """
    Menjalankan penarikan media sosial gratis, klasifikasi AI, geocoding,
    dan penyimpanan ke PostgreSQL dengan dedupe URL/teks.
    """
    print(f"\n[+] [{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Sinkronisasi Media Sosial Gratis (tanpa API key)...")

    fetchers = {
        "mastodon": fetch_mastodon,
        "bluesky": fetch_bluesky,
        "reddit": fetch_reddit,
    }
    selected = list(fetchers.keys()) if source_filter == "all" else [source_filter]

    fetched = []
    by_source = {}
    for name in selected:
        try:
            if name == "mastodon":
                results = fetch_mastodon(limit) + fetch_mastodon_public_local(limit)
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
    new_items = []

    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            for art in unique:
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
                        "UPDATE social_threats SET status_tayang = TRUE, dibuat_pada = %s WHERE id = %s;",
                        (published_at, existing[0]),
                    )
                    updated_count += 1
                    continue

                pred = predict_threat(text)
                loc = extract_location(text, default_if_none=True)
                if not loc:
                    loc = {"nama": "Nasional (Indonesia)", "provinsi": "Indonesia", "lat": -2.2, "lon": 118.0}

                refs = [{
                    "sumber": art["sumber"],
                    "url": url,
                    "judul": text[:70],
                }]

                cur.execute(
                    """
                    INSERT INTO social_threats
                    (sumber, teks, label, probabilitas, geom, lokasi_nama, url, referensi_urls, status_tayang, dikoreksi, dibuat_pada)
                    VALUES
                    (%s, %s, %s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326), %s, %s, %s::jsonb, TRUE, FALSE, %s)
                    RETURNING id;
                    """,
                    (
                        art["sumber"],
                        text,
                        pred["label"],
                        pred["probabilitas"],
                        loc["lon"],
                        loc["lat"],
                        loc["nama"],
                        url,
                        json.dumps(refs),
                        published_at,
                    ),
                )
                new_id = cur.fetchone()[0]
                inserted_count += 1
                new_items.append({
                    "id": new_id,
                    "sumber": art["sumber"],
                    "teks": text,
                    "label": pred["label"],
                    "probabilitas": pred["probabilitas"],
                    "geom": {"type": "Point", "coordinates": [loc["lon"], loc["lat"]]},
                    "lokasi_nama": loc["nama"],
                    "url": url,
                    "referensi_urls": refs,
                    "dibuat_pada": published_at.isoformat() if hasattr(published_at, "isoformat") else str(published_at),
                })

            conn.commit()

    result = {
        "status": "success",
        "source_filter": source_filter,
        "fetched": len(fetched),
        "unique": len(unique),
        "by_source": by_source,
        "inserted": inserted_count,
        "updated": updated_count,
        "new_items": new_items,
    }
    print(f"[✓] Sinkronisasi media sosial selesai: +{inserted_count} baru, {updated_count} diperbarui.")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="GeoSentry Free Social Media Scraper (no API key)")
    parser.add_argument("--limit", type=int, default=20, help="Jumlah maksimal per query per sumber")
    parser.add_argument("--source", choices=["mastodon", "bluesky", "reddit", "all"], default="all",
                        help="Batasi ke satu sumber (default: all)")
    args = parser.parse_args()

    result = sync_free_social_to_db(source_filter=args.source, limit=args.limit)
    print(json.dumps(result, indent=2))
