"""
GeoSentry - Modul Geocoding Offline
Mengekstraksi entitas nama wilayah Indonesia dari teks bebas tanpa API eksternal (Zero-Token).
Mencocokkan dengan kamus wilayah (gazetteer) secara cepat menggunakan regex word boundary.
"""

import os
import re
import sys
from dotenv import load_dotenv
import psycopg

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(dotenv_path=os.path.join(BASE_DIR, ".env"))

DATABASE_URL = os.getenv("DATABASE_URL")

# Cache memori kamus wilayah
_GAZETTEER_CACHE = None
_GAZETTEER_PATTERNS = None


def load_gazetteer_cache(conn=None):
    """
    Memuat data gazetteer dari PostgreSQL ke dalam cache memori lokal.
    Diurutkan dari nama terpanjang ke terpendek untuk mencegah substring collision
    (misal: 'jakarta selatan' harus cocok sebelum 'jakarta').
    """
    global _GAZETTEER_CACHE, _GAZETTEER_PATTERNS
    if _GAZETTEER_CACHE is not None:
        return _GAZETTEER_CACHE

    records = []
    try:
        if conn is None:
            with psycopg.connect(DATABASE_URL) as direct_conn:
                with direct_conn.cursor() as cur:
                    cur.execute("SELECT nama, provinsi, lat, lon FROM gazetteer ORDER BY LENGTH(nama) DESC;")
                    records = cur.fetchall()
        else:
            with conn.cursor() as cur:
                cur.execute("SELECT nama, provinsi, lat, lon FROM gazetteer ORDER BY LENGTH(nama) DESC;")
                records = cur.fetchall()
    except Exception as e:
        print(f"[!] Peringatan: Gagal memuat gazetteer dari DB: {e}. Menggunakan fallback lokal.", file=sys.stderr)
        # Fallback minimal kota-kota besar jika DB belum siap
        records = [
            ("jakarta pusat", "DKI Jakarta", -6.1805, 106.8284),
            ("jakarta selatan", "DKI Jakarta", -6.2615, 106.8106),
            ("jakarta timur", "DKI Jakarta", -6.2250, 106.9004),
            ("jakarta barat", "DKI Jakarta", -6.1683, 106.7589),
            ("jakarta utara", "DKI Jakarta", -6.1384, 106.8640),
            ("jakarta", "DKI Jakarta", -6.2088, 106.8456),
            ("bandung", "Jawa Barat", -6.9175, 107.6191),
            ("surabaya", "Jawa Timur", -7.2575, 112.7521),
            ("medan", "Sumatera Utara", 3.5952, 98.6722),
            ("semarang", "Jawa Tengah", -6.9667, 110.4167),
            ("makassar", "Sulawesi Selatan", -5.1477, 119.4327),
            ("yogyakarta", "DI Yogyakarta", -7.7956, 110.3695),
            ("jogja", "DI Yogyakarta", -7.7956, 110.3695),
            ("denpasar", "Bali", -8.6705, 115.2126),
            ("palembang", "Sumatera Selatan", -2.9761, 104.7754),
            ("balikpapan", "Kalimantan Timur", -1.2654, 116.8312),
            ("samarinda", "Kalimantan Timur", -0.5022, 117.1536),
        ]

    _GAZETTEER_CACHE = records

    # Bangun pola regex kompilasi dengan word boundary
    patterns = []
    for nama, prov, lat, lon in records:
        # Menangani variasi sebutan seperti 'kota bandung', 'kabupaten bandung', 'kab. bandung'
        escaped = re.escape(nama)
        regex_str = rf"\b(?:kota\s+|kabupaten\s+|kab\.\s+)?{escaped}\b"
        patterns.append((re.compile(regex_str, re.IGNORECASE), nama, prov, lat, lon))
    _GAZETTEER_PATTERNS = patterns

    return _GAZETTEER_CACHE


def clean_text_for_geocoding(text: str) -> str:
    """Membersihkan teks dan normalisasi karakter untuk pencocokan geocoding."""
    if not text:
        return ""
    # Ganti URL, mention, hashtag
    t = re.sub(r"https?://\S+|www\.\S+", " ", text)
    t = re.sub(r"[@#]\w+", " ", t)
    # Ganti karakter tanda baca tertentu dengan spasi
    t = re.sub(r"[^\w\s\.-]", " ", t)
    # Satukan spasi berlebih
    t = re.sub(r"\s+", " ", t).strip().lower()
    return t


def extract_location(text: str, default_if_none=True):
    """
    Mengekstrak lokasi geografis dari teks.
    
    Args:
        text (str): Teks cuitan atau berita
        default_if_none (bool): Jika True dan tidak ada lokasi terdeteksi,
                                kembalikan titik pusat Jakarta/Indonesia secara default.
    
    Returns:
        dict: {"nama": str, "provinsi": str, "lat": float, "lon": float} atau None
    """
    if not text:
        return None

    if _GAZETTEER_PATTERNS is None:
        load_gazetteer_cache()

    cleaned = clean_text_for_geocoding(text)

    # Cari kecocokan pertama berdasarkan urutan panjang nama (longest match first)
    for pattern, nama, prov, lat, lon in _GAZETTEER_PATTERNS:
        if pattern.search(cleaned):
            return {
                "nama": nama.title(),
                "provinsi": prov,
                "lat": float(lat),
                "lon": float(lon)
            }

    # Jika tidak ada yang cocok
    if default_if_none:
        # Default titik acuan pusat (Pusat Pemerintahan / Jakarta)
        return {
            "nama": "Nasional (Jakarta)",
            "provinsi": "DKI Jakarta",
            "lat": -6.2088,
            "lon": 106.8456
        }
    return None


if __name__ == "__main__":
    print("[+] Menguji Geocoding Offline...")
    test_samples = [
        "Waspada! Beredar kabar hoaks tsunami di pesisir pantai Gunungkidul siang ini.",
        "Aksi demo ricuh di depan kantor gubernur Jawa Barat di Kota Bandung kemarin malam.",
        "Terdeteksi akun bot menyebarkan narasi bohong mengenai pemilu di wilayah Surabaya dan sekitarnya.",
        "Kementerian Kominfo mengklarifikasi kabar hoaks bantuan dana tunai untuk warga Makassar.",
        "Isu manipulasi kuota minyak goreng berhembus kencang di Balikpapan Kalimantan Timur.",
        "Beredar narasi hoaks tanpa menyebutkan lokasi spesifik di internet.",
    ]

    for sample in test_samples:
        loc = extract_location(sample, default_if_none=False)
        print(f"\nTeks  : {sample}")
        print(f"Lokasi: {loc}")
