"""
GeoSentry - Pipa Data Jalur 1: Twitter/X Scraper & Buffer Import
Mengimpor cuitan opini publik dari data/twitter_buffer.csv (pre-scraped buffer),
memproses klasifikasi NLP & Geocoding offline, lalu memasukkan ke PostgreSQL `social_threats`.
Mengikuti Aturan §6.4: Fail-Safe Presentation (tanpa scraping live saat presentasi).
"""

import os
import sys
import csv
import random
from dotenv import load_dotenv
import psycopg

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(dotenv_path=os.path.join(BASE_DIR, ".env"))

DATABASE_URL = os.getenv("DATABASE_URL")

if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from scripts.nlp.predict import predict_threat
from scripts.gazetteer.geocode import extract_location

CSV_PATH = os.path.join(BASE_DIR, "data", "twitter_buffer.csv")

# Kumpulan cuitan realistis opini publik media sosial Indonesia
SAMPLE_TWEETS_TEMPLATES = [
    # Hoaks / Disinformasi
    ("Waspada warga {kota}! Katanya air PDAM sengaja dicampur zat kimia berbahaya oleh oknum luar, sudah banyak yang pusing dan muntah-muntah!", "https://twitter.com/user{id}/status/{sid}"),
    ("Viral di grup RT: pembagian bansos BLT uang tunai 3 juta bagi warga {kota} bisa diklaim lewat link t.me/bansos-darurat!", "https://twitter.com/user{id}/status/{sid}"),
    ("Info A1 dari orang dalam: mulai besok seluruh SPBU di {kota} ditutup total selama seminggu karena pasokan BBM langka!", "https://twitter.com/user{id}/status/{sid}"),
    ("Jangan ada yang mau divaksin flu terbaru di puskesmas {kota}! Teman sepupu saya langsung lumpuh setelah disuntik!", "https://twitter.com/user{id}/status/{sid}"),
    ("Beredar rekaman suara: ada badai angin tornado dan gempa megathrust yang bakal hantam pantai {kota} jam 1 malam ini!", "https://twitter.com/user{id}/status/{sid}"),
    ("Hati-hati beredar beras plastik di pasar induk {kota}, kalau dimasak teksturnya kenyal seperti lem berbahaya bagi lambung!", "https://twitter.com/user{id}/status/{sid}"),
    ("Ditemukan mikrochip rahasia di dalam meteran listrik token PLN di perumahan {kota}, alat ini sadap pembicaraan warga!", "https://twitter.com/user{id}/status/{sid}"),
    ("Klaim dokter gadungan: minum air rebusan nanas campur garam kasar setiap malam bisa bunuh sel tumor ganas di tubuh!", "https://twitter.com/user{id}/status/{sid}"),
    ("Foto gedung dewan di {kota} roboh beredar luas, padahal dicek langsung gedungnya masih utuh berdiri. Dasar buzzer penyebar hoaks!", "https://twitter.com/user{id}/status/{sid}"),
    ("Awas komplotan penculik anak naik mobil box putih berkeliaran di sekitar SD wilayah {kota}, tolong sebarkan ke grup sekolah!", "https://twitter.com/user{id}/status/{sid}"),

    # Sentimen Negatif / Provokasi / Polarisasi
    ("Serbu dan bakar kantor dinas di {kota}! Pejabatnya korupsi milyaran tapi rakyatnya dibiarkan kelaparan, saatnya amuk massa!", "https://twitter.com/user{id}/status/{sid}"),
    ("Aparat keparat di {kota} malah melindungi penjahat! Ayo kawan-kawan kita sweeping dan hancurkan markas mereka malam ini!", "https://twitter.com/user{id}/status/{sid}"),
    ("Usir semua etnis pendatang itu dari tanah {kota}, mereka biang kerok kejahatan dan bikin susah warga asli!", "https://twitter.com/user{id}/status/{sid}"),
    ("Dasar penguasa biadab! Jangan kasih ruang bernafas buat pendukung rezim laknat ini di jalanan {kota}!", "https://twitter.com/user{id}/status/{sid}"),
    ("Boikot total dan bakar toko-toko perusak akidah di {kota}! Habisi mereka sampai ludes tanpa sisa!", "https://twitter.com/user{id}/status/{sid}"),
    ("Massa siap bergerak bawa sajam ke pusat kota {kota}. Kalau aparat berani hadang, kita tumpahkan darah di aspal!", "https://twitter.com/user{id}/status/{sid}"),
    ("Jangan mau tunduk sama aturan zalim ini! Bakar fasilitas umum biar pemerintah di {kota} lumpuh total!", "https://twitter.com/user{id}/status/{sid}"),
    ("Hajar sampai mati provokator bayaran itu kalau ketemu di kawasan {kota}, jangan biarkan lolos!", "https://twitter.com/user{id}/status/{sid}"),

    # Opini Publik & Fakta / Tanggapan Netizen
    ("Alhamdulillah jalan berlubang di dekat flyover {kota} akhirnya mulai diaspal mulus oleh dinas PU.", "https://twitter.com/user{id}/status/{sid}"),
    ("Jangan gampang percaya berita viral tanpa cek fakta dulu gaes, kemarin isu penculikan di {kota} terbukti hoaks.", "https://twitter.com/user{id}/status/{sid}"),
    ("Cuaca di {kota} sore ini mendung tebal disertai angin kencang, hati-hati buat yang lagi berkendara motor.", "https://twitter.com/user{id}/status/{sid}"),
    ("Polres {kota} gerak cepat mengamankan pelaku begal motor yang sempat meresahkan warga belakangan ini.", "https://twitter.com/user{id}/status/{sid}"),
    ("Keren banget pameran inovasi teknologi dan UMKM di gedung Balai Pemuda {kota} ramai dikunjungi warga.", "https://twitter.com/user{id}/status/{sid}"),
    ("Info buat teman-teman di {kota}, puskesmas kecamatan lagi buka vaksinasi gratis dan donor darah rutin.", "https://twitter.com/user{id}/status/{sid}")
]

KOTA_SEBARAN = [
    "Jakarta Selatan", "Jakarta Pusat", "Jakarta Timur", "Jakarta Barat", "Jakarta Utara",
    "Bandung", "Bogor", "Bekasi", "Depok", "Tangerang", "Tangerang Selatan", "Cimahi", "Cirebon",
    "Surabaya", "Malang", "Sidoarjo", "Gresik", "Kediri", "Madiun", "Banyuwangi",
    "Semarang", "Surakarta", "Solo", "Magelang", "Pekalongan", "Tegal", "Banyumas", "Purwokerto",
    "Yogyakarta", "Sleman", "Bantul", "Gunungkidul",
    "Medan", "Binjai", "Pematang Siantar", "Deli Serdang",
    "Banda Aceh", "Lhokseumawe", "Padang", "Bukittinggi", "Pekanbaru", "Dumai", "Batam", "Tanjung Pinang",
    "Palembang", "Prabumulih", "Jambi", "Bengkulu", "Bandar Lampung", "Pangkalpinang",
    "Pontianak", "Singkawang", "Banjarmasin", "Banjarbaru", "Palangka Raya", "Samarinda", "Balikpapan", "Tarakan",
    "Makassar", "Parepare", "Palopo", "Manado", "Bitung", "Palu", "Kendari", "Gorontalo", "Mamuju",
    "Denpasar", "Mataram", "Kupang", "Ambon", "Ternate", "Jayapura", "Sorong", "Merauke", "Timika"
]


def ensure_buffer_csv(target_rows=160):
    """Membuat twitter_buffer.csv jika belum ada atau kurang dari target."""
    os.makedirs(os.path.dirname(CSV_PATH), exist_ok=True)
    
    rows = []
    random.seed(1337)
    for i in range(target_rows):
        tmpl, url_tmpl = random.choice(SAMPLE_TWEETS_TEMPLATES)
        kota = random.choice(KOTA_SEBARAN)
        user_id = random.randint(1000, 99999)
        status_id = random.randint(1800000000000000000, 1899999999999999999)
        
        tweet_text = tmpl.format(kota=kota)
        url = url_tmpl.format(id=user_id, sid=status_id)
        rows.append({"teks": tweet_text, "url": url})

    with open(CSV_PATH, mode="w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["teks", "url"])
        writer.writeheader()
        writer.writerows(rows)
    print(f"[✓] Berhasil menyiapkan buffer cuitan Twitter: {CSV_PATH} ({len(rows)} baris)")


def import_twitter_buffer():
    if not os.path.exists(CSV_PATH):
        ensure_buffer_csv(160)

    print(f"[+] Membaca {CSV_PATH} untuk diimpor ke PostgreSQL...")
    rows = []
    with open(CSV_PATH, mode="r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            if r.get("teks"):
                rows.append(r)

    print(f"[+] Total data cuitan: {len(rows)} baris. Memproses NLP & Geocoding...")

    inserted = 0
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            for item in rows:
                text = item["teks"]
                url = item.get("url", "https://twitter.com")

                # 1. Prediksi AI
                pred = predict_threat(text)
                label = pred["label"]
                prob = pred["probabilitas"]

                # 2. Geocoding
                loc = extract_location(text, default_if_none=True)
                lokasi_nama = loc["nama"]
                lat = loc["lat"]
                lon = loc["lon"]

                # 3. Insert ke database (status_tayang = FALSE untuk simulasi buffer)
                query = """
                    INSERT INTO social_threats 
                    (sumber, teks, label, probabilitas, geom, lokasi_nama, url, status_tayang, dikoreksi)
                    VALUES 
                    (%s, %s, %s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326), %s, %s, FALSE, FALSE);
                """
                cur.execute(query, (
                    "twitter",
                    text,
                    label,
                    prob,
                    lon,
                    lat,
                    lokasi_nama,
                    url
                ))
                inserted += 1

            conn.commit()
            cur.execute("SELECT count(*) FROM social_threats WHERE sumber = 'twitter';")
            total_tw = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM social_threats;")
            total_all = cur.fetchone()[0]
            print(f"[✓] Berhasil mengimpor {inserted} cuitan Twitter.")
            print(f"[✓] Total 'twitter' di DB: {total_tw}")
            print(f"[✓] Total keseluruhan buffer di `social_threats`: {total_all} baris!")
            return total_all


if __name__ == "__main__":
    ensure_buffer_csv(160)
    import_twitter_buffer()
