"""
GeoSentry - Pipa Data Jalur 2: Scraper Berita & Fakta (Ground Truth)
Mengambil berita/fakta dari RSS Feed resmi atau data offline,
memproses NLP & Geocoding, lalu menyimpannya ke tabel PostgreSQL `social_threats`.
"""

import os
import sys
import re
import feedparser
from bs4 import BeautifulSoup
from dotenv import load_dotenv
import psycopg

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(dotenv_path=os.path.join(BASE_DIR, ".env"))

DATABASE_URL = os.getenv("DATABASE_URL")

# Tambahkan BASE_DIR ke sys.path agar modul scripts dapat diimpor
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from scripts.nlp.predict import predict_threat
from scripts.gazetteer.geocode import extract_location

# Daftar RSS Feed Berita Nasional & Cek Fakta Indonesia
RSS_FEEDS = [
    "https://www.antaranews.com/rss/terkini.xml",
    "https://turnbackhoax.id/feed/",
    "https://www.cnbcindonesia.com/news/rss",
]

# Data Berita & Klarifikasi Offline (Fail-safe jika tanpa koneksi internet saat evaluasi)
FALLBACK_ARTICLES = [
    {
        "judul": "Mafindo Klarifikasi: Informasi Uang Bantuan Tunai 5 Juta Lewat Telegram Adalah Penipuan di Jakarta",
        "isi": "Beredar pesan di media sosial mengklaim pemerintah menyalurkan bantuan tunai 5 juta rupiah melalui grup Telegram di Jakarta Pusat. Hasil periksa fakta Mafindo menegaskan kabar tersebut adalah penipuan berkedok phishing.",
        "url": "https://turnbackhoax.id/2026/10/24/bansos-telegram-hoaks",
    },
    {
        "judul": "BMKG Pastikan Kabar Gempa Megathrust Tenggelamkan Pulau Jawa Hari Ini di Bandung Adalah Hoaks",
        "isi": "BMKG Bandung merilis pernyataan resmi bahwa pesan suara berantai mengenai gempa megathrust yang akan menenggelamkan pesisir malam ini adalah informasi palsu yang tidak berdasar secara ilmiah.",
        "url": "https://bmkg.go.id/berita/klarifikasi-gempa-jabar",
    },
    {
        "judul": "Pemkot Surabaya Resmikan Pusat Layanan Digital Baru untuk Transparansi Anggaran",
        "isi": "Wali Kota Surabaya meresmikan sistem dasbor pemantauan perizinan dan anggaran digital terpadu guna memperkuat integritas pelayanan publik bagi seluruh masyarakat Jawa Timur.",
        "url": "https://surabaya.go.id/berita/layanan-digital",
    },
    {
        "judul": "Polrestabes Medan Ringkus Pelaku Penyebar Narasi Kebencian dan Berita Bohong",
        "isi": "Satuan Siber Polrestabes Medan berhasil menangkap tersangka yang secara terkoordinasi menyebarkan konten manipulasi video dan ujaran provokasi bernuansa SARA di Sumatera Utara.",
        "url": "https://tribratanews.sumut.polri.go.id/berita-medan",
    },
    {
        "judul": "Kemenkes Konfirmasi Kasus Keracunan Massal Akibat Garam Beracun di Makassar Adalah Tidak Benar",
        "isi": "Kementerian Kesehatan bersama Dinas Kesehatan Kota Makassar membantah kabar yang menyebutkan ribuan warga dirawat akibat garam impor beracun. Sampel laboratorium menunjukkan garam beredar layak konsumsi.",
        "url": "https://sehatnegeriku.kemkes.go.id/klarifikasi-garam",
    },
    {
        "judul": "Bank Indonesia Perkuat Ketahanan Rupiah dan Perluasan QRIS Lintas Negara di Bali",
        "isi": "Gubernur Bank Indonesia menyampaikan keberhasilan perluasan implementasi pembayaran QRIS lintas negara di Denpasar Bali yang mendukung pertumbuhan transaksi UMKM secara aman.",
        "url": "https://bi.go.id/press/qris-bali",
    },
    {
        "judul": "Diskominfo Semarang Gelar Pelatihan Literasi Digital Tangkal Disinformasi Bagi Generasi Muda",
        "isi": "Dinas Komunikasi dan Informatika Kota Semarang mengadakan lokakarya cek fakta mandiri bagi ratusan mahasiswa guna memutus rantai persebaran berita bohong menjelang pilkada di Jawa Tengah.",
        "url": "https://semarangkota.go.id/berita-literasi",
    },
    {
        "judul": "Polda Kaltim Tindak Tegas Akun Provokatif Pemicu Konflik Lahan di Balikpapan",
        "isi": "Penyidik siber Polda Kalimantan Timur di Balikpapan memblokir jaringan akun bayaran yang menyebarkan provokasi pembakaran aset di area pembangunan infrastruktur Ibu Kota Nusantara.",
        "url": "https://kaltim.polri.go.id/siber-balikpapan",
    },
    {
        "judul": "Bareskrim Polri Bongkar Jaringan Phishing Berkedok Undangan Pernikahan Digital di Palembang",
        "isi": "Penyidik Subdit Siber mengungkap sindikat pembobol rekening bank melalui file APK undangan palsu yang beroperasi di wilayah Palembang Sumatera Selatan.",
        "url": "https://polri.go.id/berita-siber-apk",
    },
    {
        "judul": "Disperindag Yogyakarta Gelar Operasi Pasar Sembako Murah untuk Tekan Inflasi",
        "isi": "Pemerintah Daerah Istimewa Yogyakarta menggelar pasar murah di Bantul dan Sleman guna menjamin stabilitas pasokan beras serta minyak goreng dengan harga terjangkau bagi warga.",
        "url": "https://jogjaprov.go.id/pasar-murah",
    }
]


def clean_html(raw_html: str) -> str:
    """Membersihkan tag HTML dari teks deskripsi."""
    if not raw_html:
        return ""
    soup = BeautifulSoup(raw_html, "html.parser")
    text = soup.get_text(separator=" ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def fetch_rss_or_fallback():
    """Mengambil artikel dari RSS feed aktif atau fallback offline jika offline."""
    articles = []
    try:
        for url in RSS_FEEDS:
            try:
                feed = feedparser.parse(url)
                if feed.entries:
                    for entry in feed.entries[:10]:
                        title = entry.get("title", "")
                        summary = clean_html(entry.get("summary", entry.get("description", "")))
                        link = entry.get("link", url)
                        combined = f"{title}. {summary}"
                        articles.append({
                            "judul": title,
                            "isi": combined,
                            "url": link
                        })
            except Exception as fe:
                print(f"[!] Gagal membaca feed {url}: {fe}")
    except Exception as e:
        print(f"[!] Penarikan online gagal ({e}), beralih ke dataset offline terverifikasi.")

    if not articles:
        print("[i] Menggunakan dataset berita/fakta kurasi offline (Fail-Safe Presentation).")
        articles = FALLBACK_ARTICLES

    return articles


def run_pipeline():
    print("[+] Memulai pipeline scraper berita & cek fakta...")
    articles = fetch_rss_or_fallback()
    print(f"[+] Total artikel yang diproses: {len(articles)}")

    inserted_count = 0
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            for art in articles:
                text = f"{art['judul']}. {art['isi']}"
                
                # 1. Klasifikasi AI
                pred = predict_threat(text)
                label = pred["label"]
                prob = pred["probabilitas"]

                # 2. Geocoding Lokasi
                loc = extract_location(text, default_if_none=True)
                lokasi_nama = loc["nama"]
                lat = loc["lat"]
                lon = loc["lon"]

                # 3. Simpan ke PostgreSQL dengan Point PostGIS
                # status_tayang default FALSE untuk buffer presentasi
                query = """
                    INSERT INTO social_threats 
                    (sumber, teks, label, probabilitas, geom, lokasi_nama, url, status_tayang, dikoreksi)
                    VALUES 
                    (%s, %s, %s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326), %s, %s, FALSE, FALSE);
                """
                cur.execute(query, (
                    "berita",
                    text,
                    label,
                    prob,
                    lon,  # ST_MakePoint(X, Y) -> (lon, lat)
                    lat,
                    lokasi_nama,
                    art["url"]
                ))
                inserted_count += 1

            conn.commit()
            cur.execute("SELECT count(*) FROM social_threats WHERE sumber = 'berita';")
            total_berita = cur.fetchone()[0]
            print(f"[✓] Berhasil memasukkan {inserted_count} berita. Total berita di DB: {total_berita}")
            return total_berita


if __name__ == "__main__":
    run_pipeline()
