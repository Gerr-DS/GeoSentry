"""
GeoSentry - Automated Daily Viral News Ingest & Classifier
Mengambil berita viral terkini & hoaks harian dari Google News Indonesia, TurnBackHoax.ID,
dan Antara News, mengklasifikasi tingkat ancaman (hoaks, fakta, negatif) dengan NLP,
menentukan geolokasi, dan menyimpannya ke tabel `social_threats` dengan jendela 48 jam.
"""

import os
import sys
import re
import time
import json
from email.utils import parsedate_to_datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import parse_qs, unquote, urlparse
from urllib.parse import quote_plus
from datetime import datetime, timezone, timedelta
import feedparser
import requests
import socket

# Paksa resolusi IPv4 untuk menghindari delay SYN_SENT pada jaringan yang tidak memiliki rute IPv6
orig_getaddrinfo = socket.getaddrinfo
def getaddrinfo_ipv4(host, port, family=0, type=0, proto=0, flags=0):
    return orig_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)
socket.getaddrinfo = getaddrinfo_ipv4
socket.setdefaulttimeout(8)

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
from scripts.tools.verification_gate import verify_news_candidate, source_domain, is_identifiable_news_publisher
from scripts.search_news import is_trusted

RSS_SOURCES = [
    {
        "nama": "TurnBackHoax",
        "url": "https://turnbackhoax.id/feed/",
        "sumber_type": "turnbackhoax",
        "default_label": None
    },
    {
        "nama": "CekFakta",
        "url": "https://cekfakta.com/feed/?limit=20",
        # RSS item feed tidak selalu menyertakan tanggal terbit. Untuk sumber
        # ini tanggal wajib dibaca dari halaman asli; jangan menganggapnya baru.
        "sumber_type": "berita",
        "default_label": None,
        "max_items": 20,
        "require_published_date": True,
    },
    {
        "nama": "Google News Indonesia",
        "url": "https://news.google.com/rss?hl=id&gl=ID&ceid=ID:id",
        "sumber_type": "berita",
        "default_label": None
    },
    {
        "nama": "Antara News Terkini",
        "url": "https://www.antaranews.com/rss/terkini.xml",
        "sumber_type": "berita",
        "default_label": None
    },
    {
        "nama": "CNBC Indonesia",
        "url": "https://www.cnbcindonesia.com/news/rss",
        "sumber_type": "berita",
        "default_label": None
    },
]

# Satu feed pencarian untuk setiap provinsi. Kata kota/ibu kota membantu Google
# News menemukan laporan lokal walaupun judul tidak menulis nama provinsinya.
PROVINCE_SEARCHES = [
    ("Aceh", '"Aceh" OR "Banda Aceh"'),
    ("Sumatera Utara", '"Sumatera Utara" OR "Sumut" OR "Medan"'),
    ("Sumatera Barat", '"Sumatera Barat" OR "Sumbar" OR "Padang"'),
    ("Riau", '"Riau" OR "Pekanbaru"'),
    ("Kepulauan Riau", '"Kepulauan Riau" OR "Kepri" OR "Batam" OR "Tanjungpinang"'),
    ("Jambi", '"Jambi" OR "Kota Jambi"'),
    ("Sumatera Selatan", '"Sumatera Selatan" OR "Sumsel" OR "Palembang"'),
    ("Kepulauan Bangka Belitung", '"Bangka Belitung" OR "Babel" OR "Pangkalpinang"'),
    ("Bengkulu", '"Bengkulu"'),
    ("Lampung", '"Lampung" OR "Bandar Lampung"'),
    ("DKI Jakarta", '"DKI Jakarta" OR "Jakarta"'),
    ("Jawa Barat", '"Jawa Barat" OR "Jabar" OR "Bandung"'),
    ("Banten", '"Banten" OR "Serang" OR "Tangerang"'),
    ("Jawa Tengah", '"Jawa Tengah" OR "Jateng" OR "Semarang"'),
    ("DI Yogyakarta", '"DI Yogyakarta" OR "Yogyakarta" OR "DIY"'),
    ("Jawa Timur", '"Jawa Timur" OR "Jatim" OR "Surabaya"'),
    ("Bali", '"Bali" OR "Denpasar"'),
    ("Nusa Tenggara Barat", '"Nusa Tenggara Barat" OR "NTB" OR "Mataram"'),
    ("Nusa Tenggara Timur", '"Nusa Tenggara Timur" OR "NTT" OR "Kupang"'),
    ("Kalimantan Barat", '"Kalimantan Barat" OR "Kalbar" OR "Pontianak"'),
    ("Kalimantan Tengah", '"Kalimantan Tengah" OR "Kalteng" OR "Palangka Raya"'),
    ("Kalimantan Selatan", '"Kalimantan Selatan" OR "Kalsel" OR "Banjarmasin"'),
    ("Kalimantan Timur", '"Kalimantan Timur" OR "Kaltim" OR "Samarinda" OR "Balikpapan"'),
    ("Kalimantan Utara", '"Kalimantan Utara" OR "Kaltara" OR "Tarakan" OR "Tanjung Selor"'),
    ("Sulawesi Utara", '"Sulawesi Utara" OR "Sulut" OR "Manado"'),
    ("Gorontalo", '"Gorontalo"'),
    ("Sulawesi Tengah", '"Sulawesi Tengah" OR "Sulteng" OR "Palu"'),
    ("Sulawesi Barat", '"Sulawesi Barat" OR "Sulbar" OR "Mamuju"'),
    ("Sulawesi Selatan", '"Sulawesi Selatan" OR "Sulsel" OR "Makassar"'),
    ("Sulawesi Tenggara", '"Sulawesi Tenggara" OR "Sultra" OR "Kendari"'),
    ("Maluku", '"Provinsi Maluku" OR "Ambon"'),
    ("Maluku Utara", '"Maluku Utara" OR "Malut" OR "Ternate" OR "Sofifi"'),
    ("Papua", '"Provinsi Papua" OR "Jayapura"'),
    ("Papua Barat", '"Papua Barat" OR "Manokwari"'),
    ("Papua Selatan", '"Papua Selatan" OR "Merauke"'),
    ("Papua Tengah", '"Papua Tengah" OR "Nabire"'),
    ("Papua Pegunungan", '"Papua Pegunungan" OR "Wamena" OR "Jayawijaya"'),
    ("Papua Barat Daya", '"Papua Barat Daya" OR "Sorong"'),
]
RSS_SOURCES.extend({
    "nama": f"Provinsi {province}",
    "url": (
        "https://news.google.com/rss/search?q="
        f"{quote_plus(f'({query}) when:1d')}&hl=id&gl=ID&ceid=ID:id"
    ),
    "sumber_type": "berita",
    "default_label": None,
    "max_items": 5,
} for province, query in PROVINCE_SEARCHES)

RSS_FETCH_STATUS = {}
RSS_REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; GeoSentry/1.0; public news feed reader)",
    "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml;q=0.9, */*;q=0.8",
}
RSS_REQUEST_TIMEOUT = (5, 12)
RSS_REQUEST_ATTEMPTS = 2

# Fallback berita & hoaks viral kurasi offline (jika koneksi RSS sedang offline/timeout)
FALLBACK_VIRAL_DATA = [
    # Hari Ini (Today)
    {
        "judul": "[SALAH] Beredar Surat Edaran Rekrutmen Tenaga Honorer Massal Tanpa Tes di Jakarta",
        "isi": "Beredar dokumen palsu mencatut nama KemenPAN-RB mengklaim pengangkatan otomatis jutaan honorer di DKI Jakarta. MenPAN-RB menegaskan kabar tersebut adalah hoaks dan penipuan terstruktur.",
        "url": "https://turnbackhoax.id/articles/37395-salah-rekrutmen-honorer-tanpa-tes",
        "sumber": "turnbackhoax",
        "days_ago": 0,
        "kota": "Jakarta Pusat (DKI Jakarta)",
        "lat": -6.1805, "lon": 106.8284
    },
    {
        "judul": "Presiden Resmikan Sistem Peringatan Dini Cuaca Ekstrem Terintegrasi di Jawa Barat",
        "isi": "Presiden meresmikan jaringan sensor radar maritim dan peringatan dini bencana hidrometeorologi terbaru di Bandung Jawa Barat untuk mendukung kesiapsiagaan BPBD.",
        "url": "https://news.google.com/search?q=peresmian+radar+cuaca+bandung",
        "sumber": "berita",
        "days_ago": 0,
        "kota": "Bandung (Jawa Barat)",
        "lat": -6.9175, "lon": 107.6191
    },
    {
        "judul": "Aparat Polrestabes Surabaya Ringkus Sindikat Pencucian Uang dan Kejahatan Siber",
        "isi": "Satreskrim Polrestabes Surabaya membekuk sindikat peretas akun perbankan digital dan menyita ratusan gawai serta rekening penampungan di Jawa Timur.",
        "url": "https://antaranews.com/berita/siber-surabaya",
        "sumber": "berita",
        "days_ago": 0,
        "kota": "Surabaya (Jawa Timur)",
        "lat": -7.2575, "lon": 112.7521
    },
    {
        "judul": "[SALAH] Megawati Resmi Pensiun dari Partai, Pilih Ganjar jadi Ketua PDIP yang Baru",
        "isi": "Hasil periksa fakta Mafindo menegaskan klaim video pidato pengunduran diri Megawati dan penunjukan Ganjar Pranowo sebagai ketua umum PDIP adalah konten yang dimanipulasi.",
        "url": "https://turnbackhoax.id/articles/37395-salah-megawati-resmi-pensiun-dari-partai",
        "sumber": "turnbackhoax",
        "days_ago": 0,
        "kota": "Jakarta (DKI Jakarta)",
        "lat": -6.2088, "lon": 106.8456
    },
    {
        "judul": "BMKG Pastikan Aktivitas Vulkanik Gunung Merapi Terpantau Stabil Tanpa Peningkatan Status",
        "isi": "Balai Penyelidikan dan Pengembangan Teknologi Kebencanaan Geologi BPPTKG Yogyakarta menyatakan status Gunung Merapi tetap berada di level siaga dan aktivitas guguran normal.",
        "url": "https://antaranews.com/berita/merapi-yogyakarta",
        "sumber": "berita",
        "days_ago": 0,
        "kota": "Yogyakarta (DI Yogyakarta)",
        "lat": -7.7956, "lon": 110.3695
    },
    {
        "judul": "Rezim zalim dan aparat keparat di Medan harus dibakar habis jangan kasih ruang untuk mereka!",
        "isi": "Beredar seruan provokasi anarkistis di kanal Telegram mengajak massa membakar kantor instansi pemerintah dan pos polisi di Kota Medan malam ini.",
        "url": "https://twitter.com/search?q=provokasi+anarki+medan",
        "sumber": "twitter",
        "days_ago": 0,
        "kota": "Medan (Sumatera Utara)",
        "lat": 3.5952, "lon": 98.6722
    },
    {
        "judul": "[SALAH] Video Viral TikTok: Klaim Dana Bansos PKH 10 Juta Cair Hari Ini di Makassar",
        "isi": "Beredar konten video viral di aplikasi TikTok menarasikan pembagian uang tunai 10 juta tanpa syarat bagi warga Makassar. Kemensos memastikan video tersebut hoaks rekayasa AI.",
        "url": "https://www.tiktok.com/search?q=klaim+bansos+pkh+makassar",
        "sumber": "tiktok",
        "days_ago": 0,
        "kota": "Makassar (Sulawesi Selatan)",
        "lat": -5.1477, "lon": 119.4327
    },
    {
        "judul": "[SALAH] Postingan Viral Instagram: Air Mineral Tercemar Racun Mematikan di Denpasar",
        "isi": "Sebuah feed postingan Instagram menyebarkan tangkapan layar klaim air mineral kemasan beracun di Bali. BPOM Denpasar mengonfirmasi kabar itu adalah hoaks dan fitnah.",
        "url": "https://www.instagram.com/explore/tags/klarifikasibpom/",
        "sumber": "instagram",
        "days_ago": 0,
        "kota": "Denpasar (Bali)",
        "lat": -8.6705, "lon": 115.2126
    },
    {
        "judul": "Bank Indonesia Perluas Jaringan QRIS Lintas Negara ke Kawasan Asia Timur di Denpasar",
        "isi": "Gubernur Bank Indonesia resmi meluncurkan uji coba transaksi QRIS antarnegara di Denpasar Bali untuk memfasilitasi kemudahan transaksi bagi pelaku pariwisata.",
        "url": "https://bi.go.id/berita/qris-bali",
        "sumber": "berita",
        "days_ago": 0,
        "kota": "Denpasar (Bali)",
        "lat": -8.6705, "lon": 115.2126
    },
    {
        "judul": "[SALAH] Beredar Beras Plastik Kimia Mematikan Dijual Murah di Pasar Tradisional Makassar",
        "isi": "Dinas Ketahanan Pangan Makassar dan BPOM membantah video viral adanya beras plastik sintetis. Uji laboratorium sampel pasar membuktikan beras aman untuk dikonsumsi.",
        "url": "https://turnbackhoax.id/articles/37396-salah-beras-plastik-makassar",
        "sumber": "turnbackhoax",
        "days_ago": 0,
        "kota": "Makassar (Sulawesi Selatan)",
        "lat": -5.1477, "lon": 119.4327
    },

    # Kemarin (Yesterday)
    {
        "judul": "Kemenkes Salurkan 500 Ribu Dosis Vaksin dan Obat Pencegahan Polio di Semarang",
        "isi": "Dinas Kesehatan Provinsi Jawa Tengah bersama Kementerian Kesehatan menggelar Sub Pekan Imunisasi Nasional serentak di seluruh puskesmas Kota Semarang.",
        "url": "https://sehatnegeriku.kemkes.go.id/berita-semarang",
        "sumber": "berita",
        "days_ago": 1,
        "kota": "Semarang (Jawa Tengah)",
        "lat": -6.9667, "lon": 110.4167
    },
    {
        "judul": "[SALAH] Informasi A1 BMKG Peringatkan Tsunami Raksasa Terjang Pesisir Balikpapan Tengah Malam",
        "isi": "Pesan berantai mengatasnamakan BMKG Kalimantan Timur menyatakan akan terjadi gelombang tsunami 15 meter di Selat Makassar adalah hoaks rekayasa orang tak bertanggung jawab.",
        "url": "https://turnbackhoax.id/articles/37390-salah-tsunami-balikpapan",
        "sumber": "turnbackhoax",
        "days_ago": 1,
        "kota": "Balikpapan (Kalimantan Timur)",
        "lat": -1.2654, "lon": 116.8312
    },
    {
        "judul": "Polda Sumatera Selatan Ungkap Jaringan Peredaran Narkotika Lintas Provinsi di Palembang",
        "isi": "Direktorat Reserse Narkoba Polda Sumsel berhasil mengamankan 35 kilogram sabu dan ribuan pil ekstasi yang diselundupkan melalui jalur perairan Sungai Musi.",
        "url": "https://tribratanews.polri.go.id/palembang-narkoba",
        "sumber": "berita",
        "days_ago": 1,
        "kota": "Palembang (Sumatera Selatan)",
        "lat": -2.9761, "lon": 104.7754
    },
    {
        "judul": "Ayo sweeping dan usir etnis cina dari Balikpapan jangan biarkan mereka kuasai kota kita!",
        "isi": "Postingan ujaran kebencian bernada SARA dan ancaman kekerasan fisik beredar di media sosial menyasar kelompok minoritas di Kalimantan Timur.",
        "url": "https://twitter.com/search?q=sweeping+balikpapan",
        "sumber": "twitter",
        "days_ago": 1,
        "kota": "Balikpapan (Kalimantan Timur)",
        "lat": -1.2654, "lon": 116.8312
    },
    {
        "judul": "Pemkot Jayapura Salurkan Bantuan Perlengkapan Sekolah untuk Ratusan Siswa Perbatasan",
        "isi": "Wali Kota Jayapura menyerahkan paket seragam dan buku tulis bagi siswa sekolah dasar di distrik Muara Tami perbatasan Republik Indonesia dan Papua Nugini.",
        "url": "https://antaranews.com/berita/sekolah-jayapura",
        "sumber": "berita",
        "days_ago": 1,
        "kota": "Jayapura (Papua)",
        "lat": -2.5337, "lon": 140.7181
    },
    {
        "judul": "[SALAH] Link Pendaftaran Bantuan Tunai UMKM 10 Juta Lewat Saluran Telegram di Manado",
        "isi": "Kemenkop UKM mengonfirmasi bahwa tautan formulir Telegram pembagian uang tunai adalah modus penipuan phishing data rekening perbankan warga Sulawesi Utara.",
        "url": "https://turnbackhoax.id/articles/37391-salah-umkm-telegram-manado",
        "sumber": "turnbackhoax",
        "days_ago": 1,
        "kota": "Manado (Sulawesi Utara)",
        "lat": 1.4748, "lon": 124.8421
    }
]


def clean_html(raw_html: str) -> str:
    """Membersihkan tag HTML dari teks deskripsi RSS."""
    if not raw_html:
        return ""
    soup = BeautifulSoup(raw_html, "html.parser")
    text = soup.get_text(separator=" ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def resolve_feed_article_url(url):
    """Resolve Google News RSS wrappers to the original publisher article URL."""
    parsed = urlparse(url or "")
    direct_url = parse_qs(parsed.query).get("url", [""])[0]
    if direct_url:
        direct_url = unquote(direct_url)
        if is_trusted(source_domain(direct_url)):
            return direct_url
    if source_domain(url) != "news.google.com":
        return url
    try:
        response = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; GeoSentry/1.0)"},
            timeout=(2, 4),
            allow_redirects=True,
        )
        if is_trusted(source_domain(response.url)):
            return response.url
        soup = BeautifulSoup(response.content, "html.parser")
        candidates = []
        canonical = soup.find("link", rel="canonical")
        if canonical and canonical.get("href"):
            candidates.append(canonical["href"])
        for node in soup.find_all("a", href=True):
            candidate = node["href"]
            nested = parse_qs(urlparse(candidate).query).get("url", [""])[0]
            candidates.append(unquote(nested) if nested else candidate)
        return next((candidate for candidate in candidates if is_trusted(source_domain(candidate))), url)
    except requests.RequestException:
        return url


def parse_published_datetime(value):
    """Parse RFC/ISO publication dates without guessing when metadata is absent."""
    if not value:
        return None
    value = str(value).strip()
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError, OverflowError):
        try:
            parsed = parsedate_to_datetime(value)
        except (TypeError, ValueError, OverflowError):
            return None
    if parsed.tzinfo is None:
        # Tanggal tanpa zona waktu di halaman Indonesia ditafsirkan sebagai WIB.
        parsed = parsed.replace(tzinfo=timezone(timedelta(hours=7)))
    return parsed.astimezone(timezone.utc)


def fetch_article_published_at(url):
    """Read original article publication metadata for feeds without timestamps."""
    try:
        response = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; GeoSentry/1.0)"},
            timeout=(2, 4),
            allow_redirects=True,
        )
        response.raise_for_status()
        soup = BeautifulSoup(response.content, "html.parser")
        for attrs in (
            {"property": "article:published_time"},
            {"name": "article:published_time"},
            {"itemprop": "datePublished"},
            {"property": "datePublished"},
            {"name": "datePublished"},
        ):
            node = soup.find("meta", attrs=attrs)
            if node and node.get("content"):
                parsed = parse_published_datetime(node["content"])
                if parsed:
                    return parsed
        time_node = soup.find("time", attrs={"datetime": True})
        if time_node:
            parsed = parse_published_datetime(time_node.get("datetime"))
            if parsed:
                return parsed

        def dates_in_json(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    if key.casefold() == "datepublished":
                        parsed = parse_published_datetime(item)
                        if parsed:
                            return parsed
                    parsed = dates_in_json(item)
                    if parsed:
                        return parsed
            elif isinstance(value, list):
                for item in value:
                    parsed = dates_in_json(item)
                    if parsed:
                        return parsed
            return None

        for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
            try:
                parsed = dates_in_json(json.loads(script.string or script.get_text()))
            except (TypeError, ValueError, json.JSONDecodeError):
                parsed = None
            if parsed:
                return parsed
    except requests.RequestException:
        return None
    return None


def fetch_rss_source(source):
    """Fetch one feed with bounded retries and preserve network/parser errors."""
    last_error = None
    for attempt in range(RSS_REQUEST_ATTEMPTS):
        try:
            response = requests.get(
                source["url"],
                headers=RSS_REQUEST_HEADERS,
                timeout=RSS_REQUEST_TIMEOUT,
                allow_redirects=True,
            )
            response.raise_for_status()
            feed = feedparser.parse(response.content)
            if getattr(feed, "bozo", False) and not feed.entries:
                parse_error = getattr(feed, "bozo_exception", None)
                raise ValueError(f"RSS tidak dapat diparse: {parse_error or 'format tidak dikenal'}")
            return feed, None
        except requests.HTTPError as exc:
            code = exc.response.status_code if exc.response is not None else None
            last_error = f"HTTP {code}: {exc}"
            # Authentication, not-found, and throttling responses are not
            # improved by repeating the same request immediately.
            if code is not None and 400 <= code < 500:
                break
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"

        if attempt + 1 < RSS_REQUEST_ATTEMPTS:
            time.sleep(0.5 * (attempt + 1))

    return None, last_error or "feed gagal diambil tanpa detail error"


def fetch_live_viral_rss():
    """Fetch national feeds and province searches concurrently, then resolve publisher links."""
    articles = []
    pending = []
    RSS_FETCH_STATUS.clear()
    now = datetime.now(timezone.utc)
    cutoff_24h = now - timedelta(hours=24) # Rolling 24 jam

    with ThreadPoolExecutor(max_workers=12) as executor:
        future_sources = {executor.submit(fetch_rss_source, src): src for src in RSS_SOURCES}
        for future in as_completed(future_sources):
            src = future_sources[future]
            try:
                feed, fetch_error = future.result()
                if fetch_error:
                    RSS_FETCH_STATUS[src["nama"]] = {
                        "status": "tidak_tersedia",
                        "jumlah": 0,
                        "alasan": fetch_error,
                    }
                    print(f"[!] Gagal mengambil feed {src['nama']}: {fetch_error}", file=sys.stderr)
                    continue
                if not feed.entries:
                    RSS_FETCH_STATUS[src["nama"]] = {
                        "status": "kosong",
                        "jumlah": 0,
                        "alasan": "feed berhasil diakses, tetapi tidak berisi artikel",
                    }
                    continue

                RSS_FETCH_STATUS[src["nama"]] = {
                    "status": "berhasil",
                    "jumlah": 0,
                    **(
                        {"peringatan": str(feed.bozo_exception)}
                        if getattr(feed, "bozo", False) else {}
                    ),
                }

                for entry in feed.entries[:src.get("max_items", 30)]:
                    raw_summary = entry.get("summary", entry.get("description", ""))
                    summary = clean_html(raw_summary)
                    raw_title = entry.get("title")
                    title = str(raw_title).strip() if raw_title else ""
                    if not title or title.lower() in ["none", "null"]:
                        title = summary[:120].strip() if summary else ""
                    if not title:
                        continue

                    published_dt = None
                    if hasattr(entry, "published_parsed") and entry.published_parsed:
                        published_dt = datetime.fromtimestamp(time.mktime(entry.published_parsed), tz=timezone.utc)
                    elif hasattr(entry, "updated_parsed") and entry.updated_parsed:
                        published_dt = datetime.fromtimestamp(time.mktime(entry.updated_parsed), tz=timezone.utc)

                    if not published_dt and not src.get("require_published_date"):
                        published_dt = now
                    if published_dt and published_dt < cutoff_24h:
                        continue

                    combined_text = f"{title}. {summary}" if summary else title
                    publisher = entry.get("source") or {}
                    pending.append({
                        "judul": title,
                        "isi": combined_text,
                        "feed_url": entry.get("link", src["url"]),
                        "publisher_url": publisher.get("href"),
                        "publisher_name": publisher.get("title"),
                        "sumber": src["sumber_type"],
                        "nama_feed": src["nama"],
                        "default_label": src.get("default_label"),
                        "published_at": published_dt,
                        "require_published_date": src.get("require_published_date", False),
                    })
                    RSS_FETCH_STATUS[src["nama"]]["jumlah"] += 1
            except Exception as e:
                print(f"[!] Gagal menarik feed {src['nama']}: {e}", file=sys.stderr)
                RSS_FETCH_STATUS[src["nama"]] = {
                    "status": "tidak_tersedia", "jumlah": 0,
                    "alasan": f"{type(e).__name__}: {e}",
                }

    # Google News wraps publisher URLs. Resolve links concurrently so adding
    # 38 province feeds does not turn one hourly pass into a serial crawl.
    def resolve_pending(item):
        link = resolve_feed_article_url(item["feed_url"])
        published = item["published_at"]
        if published is None and item["require_published_date"]:
            published = fetch_article_published_at(link)
        return item, link, published

    with ThreadPoolExecutor(max_workers=12) as executor:
        resolved = list(executor.map(resolve_pending, pending))
    for item, link, published_dt in resolved:
        item["published_at"] = published_dt
        if item["require_published_date"] and (
            not published_dt
            or published_dt < cutoff_24h
            or published_dt > now + timedelta(minutes=5)
        ):
            RSS_FETCH_STATUS[item["nama_feed"]]["jumlah"] -= 1
            continue
        item["url"] = link
        item["referensi_urls"] = [{"sumber": item["sumber"], "url": link, "judul": item["judul"]}]
        publisher_url = item.get("publisher_url")
        publisher_domain = source_domain(publisher_url)
        if (
            source_domain(link) == "news.google.com"
            and is_identifiable_news_publisher(publisher_url)
        ):
            item["referensi_urls"].append({
                "sumber": item.get("publisher_name") or publisher_domain,
                "url": publisher_url,
                "judul": f"Situs penerbit: {item.get('publisher_name') or publisher_domain}",
            })
        item.pop("feed_url", None)
        item.pop("require_published_date", None)
        articles.append(item)

    return articles


def sync_viral_threats_to_db():
    """
    Menjalankan proses penarikan berita viral harian, klasifikasi AI,
    dan penyimpanan ke PostgreSQL dengan filter jendela 48 jam.
    """
    print(f"\n[+] [{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Memulai Sinkronisasi Berita Viral Harian...")
    
    live_articles = fetch_live_viral_rss()
    print(f"[i] Berhasil menarik {len(live_articles)} artikel online dari RSS feeds.")

    inserted_count = 0
    updated_count = 0
    withheld_count = 0
    newly_inserted_items = []
    verified_by_feed = {name: 0 for name in RSS_FETCH_STATUS}
    provisional_by_feed = {name: 0 for name in RSS_FETCH_STATUS}
    withheld_by_feed = {name: 0 for name in RSS_FETCH_STATUS}

    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            for art in live_articles:
                text = art["isi"]
                url = art["url"]
                published_at = art["published_at"]
                verification = verify_news_candidate(
                    art.get("judul", ""), text, url,
                    publisher_url=art.get("publisher_url"),
                    publisher_name=art.get("publisher_name"),
                )
                if not verification:
                    withheld_count += 1
                    feed_name = art.get("nama_feed", "sumber lain")
                    withheld_by_feed[feed_name] = withheld_by_feed.get(feed_name, 0) + 1
                    continue
                feed_name = art.get("nama_feed", "sumber lain")
                verification_status = verification.get("status", "terverifikasi")
                if verification_status == "terverifikasi":
                    verified_by_feed[feed_name] = verified_by_feed.get(feed_name, 0) + 1
                elif verification_status == "prediksi_sementara":
                    provisional_by_feed[feed_name] = provisional_by_feed.get(feed_name, 0) + 1
                domain = source_domain(url)
                if domain.endswith("turnbackhoax.id"):
                    sumber_final = "turnbackhoax"
                elif domain.endswith(("cekfakta.com", "cekfakta.tempo.co", "mafindo.or.id")):
                    # Sumber rinci tetap ada di URL referensi; kelompokkan di
                    # dashboard sebagai berita agar nama TurnBackHoax tidak keliru.
                    sumber_final = "berita"
                else:
                    sumber_final = art["sumber"]

                # 1. Cek apakah artikel dengan URL atau judul serupa sudah ada
                cur.execute("SELECT id, url, referensi_urls FROM social_threats WHERE url = %s OR teks = %s LIMIT 1;", (url, text))
                existing = cur.fetchone()

                if existing:
                    existing_id, existing_url, existing_refs = existing
                    if not existing_refs:
                        existing_refs = []
                    elif isinstance(existing_refs, str):
                        try:
                            existing_refs = json.loads(existing_refs)
                        except Exception:
                            existing_refs = []

                    urls_in_refs = {r.get("url") if isinstance(r, dict) else r for r in existing_refs}
                    urls_in_refs.add(existing_url)

                    # Jika ada URL rujukan baru dari berita/isu yang sama, gabungkan ke referensi_urls
                    has_new_ref = False
                    article_refs = art.get("referensi_urls", [{"sumber": art["sumber"], "url": url, "judul": art.get("judul", "Laporan Sumber Terkait")}])
                    for r in article_refs:
                        if r.get("url") and r["url"] not in urls_in_refs:
                            existing_refs.append(r)
                            urls_in_refs.add(r["url"])
                            has_new_ref = True

                    if has_new_ref:
                        cur.execute("""
                            UPDATE social_threats 
                            SET status_tayang = TRUE, dibuat_pada = %s, referensi_urls = %s::jsonb,
                                label = %s, probabilitas = %s, verifikasi_status = %s,
                                verifikasi_dasar = %s, verifikasi_pada = NOW()
                            WHERE id = %s;
                        """, (published_at, json.dumps(existing_refs), verification["label"], verification["probabilitas"], verification_status, verification["basis"], existing_id))
                    else:
                        cur.execute("""
                            UPDATE social_threats 
                            SET status_tayang = TRUE, dibuat_pada = %s, label = %s,
                                probabilitas = %s, verifikasi_status = %s,
                                verifikasi_dasar = %s, verifikasi_pada = NOW()
                            WHERE id = %s;
                        """, (published_at, verification["label"], verification["probabilitas"], verification_status, verification["basis"], existing_id))
                    updated_count += 1
                    continue

                # Label comes from an explicit source verdict, not a model guess.
                label = verification["label"]
                prob = verification["probabilitas"]

                # 4. Geocoding Lokasi Spasial
                if "forced_loc" in art:
                    loc = art["forced_loc"]
                else:
                    loc = extract_location(text, default_if_none=True)

                lokasi_nama = loc["nama"]
                lat = loc["lat"]
                lon = loc["lon"]

                # Siapkan daftar semua tautan referensi terkait
                initial_refs = art.get("referensi_urls", [
                    {"sumber": sumber_final, "url": url, "judul": art.get("judul", text[:70])}
                ])

                # 5. Simpan ke Database PostgreSQL PostGIS dengan referensi_urls lengkap
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
                    sumber_final,
                    text,
                    label,
                    prob,
                    lon,
                    lat,
                    lokasi_nama,
                    url,
                    json.dumps(initial_refs),
                    verification_status,
                    verification["basis"],
                    published_at
                ))
                new_id = cur.fetchone()[0]
                newly_inserted_items.append({
                    "id": new_id,
                    "sumber": sumber_final,
                    "teks": text,
                    "label": label,
                    "probabilitas": prob,
                    "geom": {"type": "Point", "coordinates": [lon, lat]},
                    "lokasi_nama": lokasi_nama,
                    "url": url,
                    "referensi_urls": initial_refs,
                    "verifikasi_status": verification_status,
                    "verifikasi_dasar": verification["basis"],
                    "dibuat_pada": published_at.isoformat() if hasattr(published_at, "isoformat") else str(published_at)
                })
                inserted_count += 1

            conn.commit()

            # Verifikasi jumlah berita dalam jendela 24 jam
            cur.execute("""
                SELECT 
                    COUNT(*) as total_24h,
                    COUNT(*) FILTER (WHERE label = 'hoaks') as hoaks,
                    COUNT(*) FILTER (WHERE label = 'fakta') as fakta,
                    COUNT(*) FILTER (WHERE label = 'negatif') as negatif
                FROM social_threats
                WHERE status_tayang = TRUE 
                  AND dibuat_pada >= (NOW() - INTERVAL '24 hours');
            """)
            stats = cur.fetchone()

    result = {
        "status": "success",
        "fetched": len(live_articles),
        "source_status": {
            name: {
                **status,
                "terverifikasi": verified_by_feed.get(name, 0),
                "prediksi_sementara": provisional_by_feed.get(name, 0),
                "ditahan": withheld_by_feed.get(name, 0),
            }
            for name, status in RSS_FETCH_STATUS.items()
        },
        "inserted": inserted_count,
        "updated": updated_count,
        "withheld": withheld_count,
        "total_active_24h": stats[0],
        "hoaks": stats[1],
        "fakta": stats[2],
        "negatif": stats[3],
        "new_items": newly_inserted_items
    }
    print(f"[✓] Sinkronisasi Selesai: +{inserted_count} baru, {updated_count} diperbarui. Total 24 Jam Aktif: {stats[0]}")
    return result


if __name__ == "__main__":
    res = sync_viral_threats_to_db()
    import json
    print(json.dumps(res, indent=2))
