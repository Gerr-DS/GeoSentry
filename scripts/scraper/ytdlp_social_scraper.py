"""
GeoSentry - Direct Social Media Scraper & Hoax Verifier via yt-dlp
Mengambil metadata langsung (caption, judul, deskripsi, uploader, timestamp) dari:
- TikTok (video / postingan)
- Instagram (Reels / Post / Video)
- YouTube (Shorts / Video)
- X / Twitter (video / cuitan)
- Mastodon / Bluesky

Kemudian secara otomatis memprosesnya ke:
1. Model AI NLP Lokal (`predict_threat`) -> Klasifikasi Hoaks / Fakta / Sentimen Negatif
2. Geocoding Offline PostGIS (`extract_location`) -> Ekstraksi koordinat & 38 Provinsi Indonesia
3. Database `social_threats` (PostGIS)
"""

import os
import sys
import re
import json
import time
import socket
import argparse
from datetime import datetime, timezone
from urllib.parse import urlparse, unquote

# 1. Paksa resolusi IPv4 untuk menghindari delay koneksi SYN_SENT
orig_getaddrinfo = socket.getaddrinfo
def getaddrinfo_ipv4(host, port, family=0, type=0, proto=0, flags=0):
    return orig_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)
socket.getaddrinfo = getaddrinfo_ipv4
socket.setdefaulttimeout(8)

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
import psycopg
import yt_dlp

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(dotenv_path=os.path.join(BASE_DIR, ".env"))

DATABASE_URL = os.getenv("DATABASE_URL")

if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from scripts.nlp.predict import predict_threat
from scripts.gazetteer.geocode import extract_location
from scripts.tools.verification_gate import verify_social_post

HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "id,en-US;q=0.7,en;q=0.3"
}


def detect_platform(url: str) -> str:
    """Mendeteksi platform media sosial dari URL."""
    low = url.lower()
    if "tiktok.com" in low:
        return "tiktok"
    elif "instagram.com" in low:
        return "instagram"
    elif "twitter.com" in low or "x.com" in low:
        return "twitter"
    elif "youtube.com" in low or "youtu.be" in low:
        return "youtube"
    elif "mastodon" in low:
        return "mastodon"
    elif "bsky.app" in low:
        return "bluesky"
    return "social_media"


def extract_media_info_ytdlp(url: str) -> dict:
    """
    Mengekstrak informasi konten media sosial secara langsung menggunakan yt-dlp.
    Mengambil judul, deskripsi/caption, uploader, timestamp, dan tags.
    """
    ydl_opts = {
        "skip_download": True,
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 6,
        "extract_flat": False,
        "http_headers": HTTP_HEADERS
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if not info:
                return {}

            title = info.get("title") or ""
            desc = info.get("description") or ""
            uploader = info.get("uploader") or info.get("channel") or info.get("uploader_id") or ""
            tags = info.get("tags") or []
            upload_date = info.get("upload_date") or ""
            
            # Format teks gabungan untuk analisis AI
            combined_text = f"{title}. {desc}".strip()
            if tags and isinstance(tags, list):
                combined_text += f" {' '.join(['#' + t for t in tags if t])}"

            return {
                "success": True,
                "engine": "yt-dlp",
                "title": title,
                "description": desc,
                "combined_text": combined_text,
                "uploader": uploader,
                "upload_date": upload_date,
                "url": info.get("webpage_url") or url
            }
    except Exception as exc:
        err_msg = str(exc)
        # Jika yt-dlp gagal (misal IP diblokir oleh TikTok/IG), kita return status error agar beralih ke fallback
        return {
            "success": False,
            "engine": "yt-dlp",
            "error": err_msg
        }


def extract_media_info_fallback(url: str) -> dict:
    """
    Fallback extractor jika yt-dlp terhambat IP block atau login wall:
    Memanfaatkan oEmbed publik resmi dan OpenGraph meta parser.
    """
    platform = detect_platform(url)
    title = ""
    desc = ""
    author = ""

    # 1. Coba oEmbed TikTok jika platform TikTok
    if platform == "tiktok":
        try:
            oembed_url = f"https://www.tiktok.com/oembed?url={url}"
            resp = requests.get(oembed_url, headers=HTTP_HEADERS, timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                title = data.get("title") or ""
                author = data.get("author_name") or ""
        except Exception:
            pass

    # 2. Coba request HTML OpenGraph jika teks masih minim
    if not title:
        try:
            resp = requests.get(url, headers=HTTP_HEADERS, timeout=5)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "html.parser")
                og_title = soup.find("meta", property="og:title")
                og_desc = soup.find("meta", property="og:description")
                if og_title and og_title.get("content"):
                    title = og_title["content"]
                if og_desc and og_desc.get("content"):
                    desc = og_desc["content"]
                if not title and soup.title:
                    title = soup.title.string or ""
        except Exception:
            pass

    # 3. Jika tetap kosong, ekstrak kata kunci dari slug URL
    if not title and not desc:
        path_slug = unquote(urlparse(url).path.replace("/", " ").replace("-", " ").replace("_", " "))
        title = f"Postingan {platform.capitalize()}: {path_slug.strip()}"

    combined_text = f"{title}. {desc}".strip()
    return {
        "success": bool(combined_text),
        "engine": "fallback_oembed",
        "title": title,
        "description": desc,
        "combined_text": combined_text,
        "uploader": author or platform,
        "url": url
    }


def inspect_and_verify_social_url(url: str, custom_text: str = None, save_to_db: bool = True) -> dict:
    """
    Mengambil konten dari link medsos, memverifikasi status hoaks dengan NLP,
    mendeteksi geolokasi ke 38 provinsi, dan menyimpannya ke database PostGIS.
    """
    platform = detect_platform(url)
    
    # 1. Ekstraksi Konten via yt-dlp
    extracted = extract_media_info_ytdlp(url)
    if not extracted.get("success") or not extracted.get("combined_text"):
        # Jalankan fallback extractor jika yt-dlp terhambat
        extracted = extract_media_info_fallback(url)

    text_to_analyze = (custom_text or extracted.get("combined_text") or "").strip()
    if not text_to_analyze:
        text_to_analyze = f"Laporan pantauan postingan video viral {platform} dari tautan {url}"

    # 2. Prediksi awal, lalu bukti gerbang wajib sebelum simpan/tampilkan.
    pred = predict_threat(text_to_analyze)
    # Only trust content actually returned by yt-dlp from the original post.
    # Fallback metadata, URL slugs, and caller-supplied text are not evidence that
    # the linked post exists or expresses the analyzed sentiment.
    fetched_original_text = extracted.get("combined_text") or ""
    has_original_content = (
        extracted.get("success") is True
        and extracted.get("engine") == "yt-dlp"
        and bool(fetched_original_text.strip())
        and not custom_text
    )
    verification = verify_social_post(
        platform,
        fetched_original_text,
        url,
        original_content_fetched=has_original_content,
    )
    label = verification["label"] if verification else pred["label"]
    prob = verification["probabilitas"] if verification else pred["probabilitas"]

    # 3. Geocoding Offline PostGIS (Mencocokkan ke 38 Provinsi)
    loc = extract_location(text_to_analyze, default_if_none=True)
    lokasi_nama = loc["nama"]
    lat = loc["lat"]
    lon = loc["lon"]

    # Ekstraksi nama provinsi
    provinsi = "Nasional"
    if "(" in lokasi_nama and ")" in lokasi_nama:
        provinsi = lokasi_nama.split("(")[-1].replace(")", "").strip()

    # 4. Susun Daftar Tautan Referensi
    referensi_urls = [{
        "sumber": platform,
        "url": url,
        "judul": extracted.get("title") or f"Postingan Asli {platform.upper()}"
    }]
    if verification:
        referensi_urls.extend(verification.get("referensi", []))

    now = datetime.now(timezone.utc)
    new_id = None

    # 5. Simpan ke Database PostgreSQL PostGIS jika diminta
    if save_to_db and DATABASE_URL and verification:
        try:
            with psycopg.connect(DATABASE_URL) as conn:
                with conn.cursor() as cur:
                    # Cek duplikasi URL
                    cur.execute("SELECT id FROM social_threats WHERE url = %s LIMIT 1;", (url,))
                    exist = cur.fetchone()
                    if exist:
                        new_id = exist[0]
                        cur.execute("""
                            UPDATE social_threats 
                            SET teks = %s, label = %s, probabilitas = %s, dibuat_pada = %s, status_tayang = TRUE,
                                referensi_urls = %s::jsonb, verifikasi_status = %s,
                                verifikasi_dasar = %s, verifikasi_pada = NOW()
                            WHERE id = %s;
                        """, (text_to_analyze, label, prob, now, json.dumps(referensi_urls), verification["status"], verification["basis"], new_id))
                    else:
                        query = """
                            INSERT INTO social_threats 
                            (sumber, teks, label, probabilitas, geom, lokasi_nama, url, referensi_urls,
                             status_tayang, verifikasi_status, verifikasi_dasar, verifikasi_pada, dikoreksi, dibuat_pada)
                            VALUES 
                            (%s, %s, %s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326), %s, %s, %s::jsonb,
                             TRUE, %s, %s, NOW(), FALSE, %s)
                            RETURNING id;
                        """
                        cur.execute(query, (
                            platform,
                            text_to_analyze,
                            label,
                            prob,
                            lon,
                            lat,
                            lokasi_nama,
                            url,
                            json.dumps(referensi_urls),
                            verification["status"],
                            verification["basis"],
                            now
                        ))
                        new_id = cur.fetchone()[0]
                    conn.commit()
        except Exception as db_err:
            print(f"[!] Gagal menyimpan ke DB: {db_err}", file=sys.stderr)

    return {
        "status": "success",
        "id": new_id,
        "sumber": platform,
        "engine_used": extracted.get("engine", "direct"),
        "title": extracted.get("title") or text_to_analyze[:80],
        "teks": text_to_analyze,
        "label": label,
        "probabilitas": prob,
        "lokasi_nama": lokasi_nama,
        "provinsi": provinsi,
        "lat": lat,
        "lon": lon,
        "url": url,
        "referensi_urls": referensi_urls,
        "verifikasi_status": verification["status"] if verification else "belum terverifikasi",
        "verifikasi_dasar": verification["basis"] if verification else None,
        "dibuat_pada": now.isoformat()
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="GeoSentry yt-dlp Social Scraper & Hoax Verifier")
    parser.add_argument("--url", type=str, help="URL media sosial (TikTok, Instagram, YouTube, Twitter)")
    parser.add_argument("--text", type=str, help="Teks tambahan opsional", default=None)
    parser.add_argument("--no-save", action="store_true", help="Jangan simpan ke database")

    args = parser.parse_args()

    if args.url:
        result = inspect_and_verify_social_url(args.url, custom_text=args.text, save_to_db=not args.no_save)
        print(json.dumps(result, indent=2))
    else:
        print(json.dumps({"error": "Silakan sertakan argumen --url <URL_MEDSOS>"}))
