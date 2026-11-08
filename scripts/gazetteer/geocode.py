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


# Aliases singkatan provinsi populer di media berita Indonesia
PROVINCE_ALIASES = [
    (re.compile(r"\b(?:kalteng|kalimantan\s+tengah)\b", re.I), "Palangka Raya", "Kalimantan Tengah", -2.2079, 113.9163),
    (re.compile(r"\b(?:kalbar|kalimantan\s+barat)\b", re.I), "Pontianak", "Kalimantan Barat", -0.0263, 109.3425),
    (re.compile(r"\b(?:kalsel|kalimantan\s+selatan)\b", re.I), "Banjarmasin", "Kalimantan Selatan", -3.3194, 114.5908),
    (re.compile(r"\b(?:kaltim|kalimantan\s+timur)\b", re.I), "Samarinda", "Kalimantan Timur", -0.5022, 117.1536),
    (re.compile(r"\b(?:kaltara|kalimantan\s+utara)\b", re.I), "Tanjung Selor", "Kalimantan Utara", 2.8375, 117.3653),
    (re.compile(r"\b(?:jabar|jawa\s+barat)\b", re.I), "Bandung", "Jawa Barat", -6.9175, 107.6191),
    (re.compile(r"\b(?:jateng|jawa\s+tengah)\b", re.I), "Semarang", "Jawa Tengah", -6.9667, 110.4167),
    (re.compile(r"\b(?:jatim|jawa\s+timur)\b", re.I), "Surabaya", "Jawa Timur", -7.2575, 112.7521),
    (re.compile(r"\b(?:dki|jakarta)\b", re.I), "Jakarta", "DKI Jakarta", -6.2088, 106.8456),
    (re.compile(r"\b(?:sumut|sumatera\s+utara)\b", re.I), "Medan", "Sumatera Utara", 3.5952, 98.6722),
    (re.compile(r"\b(?:sumbar|sumatera\s+barat)\b", re.I), "Padang", "Sumatera Barat", -0.9471, 100.4172),
    (re.compile(r"\b(?:sumsel|sumatera\s+selatan)\b", re.I), "Palembang", "Sumatera Selatan", -2.9761, 104.7754),
    (re.compile(r"\b(?:sulsel|sulawesi\s+selatan)\b", re.I), "Makassar", "Sulawesi Selatan", -5.1477, 119.4327),
    (re.compile(r"\b(?:sulteng|sulawesi\s+tengah)\b", re.I), "Palu", "Sulawesi Tengah", -0.9003, 119.8779),
    (re.compile(r"\b(?:sultra|sulawesi\s+tenggara)\b", re.I), "Kendari", "Sulawesi Tenggara", -3.9985, 122.5126),
    (re.compile(r"\b(?:sulut|sulawesi\s+utara)\b", re.I), "Manado", "Sulawesi Utara", 1.4748, 124.8421),
    (re.compile(r"\b(?:papua|jayapura)\b", re.I), "Jayapura", "Papua", -2.5337, 140.7181),
    (re.compile(r"\b(?:bali|denpasar)\b", re.I), "Denpasar", "Bali", -8.6705, 115.2126),
    (re.compile(r"\b(?:aceh|serambi\s+mekkah)\b", re.I), "Banda Aceh", "Aceh", 5.5483, 95.3238),
    (re.compile(r"\b(?:ntb|lombok|mataram)\b", re.I), "Mataram", "Nusa Tenggara Barat", -8.5833, 116.1167),
    (re.compile(r"\b(?:ntt|kupang|flores)\b", re.I), "Kupang", "Nusa Tenggara Timur", -10.1772, 123.6070),
    (re.compile(r"\b(?:maluku|ambon)\b", re.I), "Ambon", "Maluku", -3.6547, 128.1906),
    (re.compile(r"\b(?:lampung|bandar\s+lampung)\b", re.I), "Bandar Lampung", "Lampung", -5.4500, 105.2667),
    (re.compile(r"\b(?:riau|pekanbaru)\b", re.I), "Pekanbaru", "Riau", 0.5071, 101.4478),
    (re.compile(r"\b(?:banten|serang|tangerang)\b", re.I), "Serang", "Banten", -6.1104, 106.1640),
]

# Hub persebaran nasional jika berita berjangkauan nasional tanpa nama kota spesifik
NATIONAL_HUBS = [
    ("Jakarta Pusat", "DKI Jakarta", -6.1805, 106.8284),
    ("Surabaya", "Jawa Timur", -7.2575, 112.7521),
    ("Bandung", "Jawa Barat", -6.9175, 107.6191),
    ("Medan", "Sumatera Utara", 3.5952, 98.6722),
    ("Makassar", "Sulawesi Selatan", -5.1477, 119.4327),
    ("Semarang", "Jawa Tengah", -6.9667, 110.4167),
    ("Palembang", "Sumatera Selatan", -2.9761, 104.7754),
    ("Denpasar", "Bali", -8.6705, 115.2126),
    ("Balikpapan", "Kalimantan Timur", -1.2654, 116.8312),
    ("Yogyakarta", "DI Yogyakarta", -7.7956, 110.3695),
    ("Banjarmasin", "Kalimantan Selatan", -3.3194, 114.5908),
    ("Pontianak", "Kalimantan Barat", -0.0263, 109.3425),
    ("Manado", "Sulawesi Utara", 1.4748, 124.8421),
    ("Jayapura", "Papua", -2.5337, 140.7181),
    ("Banda Aceh", "Aceh", 5.5483, 95.3238),
    ("Padang", "Sumatera Barat", -0.9471, 100.4172),
    ("Pekanbaru", "Riau", 0.5071, 101.4478),
    ("Mataram", "Nusa Tenggara Barat", -8.5833, 116.1167),
    ("Kupang", "Nusa Tenggara Timur", -10.1772, 123.6070),
    ("Ambon", "Maluku", -3.6547, 128.1906),
]


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
                                distribusikan secara seimbang ke hub nasional.
    
    Returns:
        dict: {"nama": str, "provinsi": str, "lat": float, "lon": float} atau None
    """
    if not text:
        return None

    if _GAZETTEER_PATTERNS is None:
        load_gazetteer_cache()

    cleaned = clean_text_for_geocoding(text)

    # 1. Cari kecocokan pertama di gazetteer spesifik (longest match first)
    for pattern, nama, prov, lat, lon in _GAZETTEER_PATTERNS:
        if pattern.search(cleaned):
            return {
                "nama": nama.title(),
                "provinsi": prov,
                "lat": float(lat),
                "lon": float(lon)
            }

    # 2. Cari kecocokan di singkatan/alias provinsi
    for pattern, nama_ibukota, prov, lat, lon in PROVINCE_ALIASES:
        if pattern.search(cleaned):
            return {
                "nama": f"{nama_ibukota} ({prov})",
                "provinsi": prov,
                "lat": float(lat),
                "lon": float(lon)
            }

    # 3. Jika tidak ada nama lokasi spesifik terdeteksi
    if default_if_none:
        # Distribusikan secara deterministik ke hub kota nasional berdasarkan hash teks
        hub_idx = abs(hash(text)) % len(NATIONAL_HUBS)
        hub = NATIONAL_HUBS[hub_idx]
        return {
            "nama": f"{hub[0]} ({hub[1]})",
            "provinsi": hub[1],
            "lat": float(hub[2]),
            "lon": float(hub[3])
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
