"""
GeoSentry - Seeder Sebaran Nasional (Sabang sampai Merauke)
Mengisi tabel `social_threats` dengan 250+ data ancaman siber sosial, disinformasi,
dan berita terverifikasi yang tersebar merata di 38 provinsi di seluruh kepulauan Indonesia.
- 120 Titik berstatus TAYANG (status_tayang = TRUE): Langsung terlihat di peta saat dibuka.
- 130+ Titik berstatus BUFFER (status_tayang = FALSE): Untuk diinjeksikan secara live oleh simulator.
"""

import os
import sys
import random
import urllib.parse
from dotenv import load_dotenv
import psycopg

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(dotenv_path=os.path.join(BASE_DIR, ".env"))

DATABASE_URL = os.getenv("DATABASE_URL")

if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from scripts.nlp.predict import predict_threat

# Kumpulan template peristiwa siber sosial Indonesia
TEMPLATES = [
    # HOAKS
    ("hoaks", "Viral di grup WA warga {kota}: klaim bantuan bansos BLT 5 juta rupiah per KK cair hari ini, segera daftar nomor HP di tautan link t.me/bansos-gratis-{slug}!"),
    ("hoaks", "Postingan viral Instagram akun warga {kota}: peringatan darurat gempa megathrust dahsyat magnitudo 9.5 akan menenggelamkan pesisir pantai {kota} malam ini jam 1 dini hari!"),
    ("hoaks", "Tangkapan layar Instagram edaran palsu: air PDAM di kawasan {kota} sengaja dicampur serbuk racun mematikan oleh oknum tidak bertanggung jawab!"),
    ("hoaks", "Feed Instagram viral warga {kota}: hati-hati beras plastik berbahan lem kimia beredar luas di pasar tradisional {kota}!"),
    ("hoaks", "Video viral TikTok di {kota}: jangan mau disuntik vaksin penguat terbaru di puskesmas, ada microchip asing yang ditanam!"),
    ("hoaks", "Konten TikTok FYP di {kota}: waspada komplotan penculik anak menyamar naik mobil box putih berkeliaran di lingkungan SD!"),
    ("hoaks", "Beredar video TikTok viral di {kota}: minum air rebusan pare campur garam kasar setiap malam membunuh sel kanker ganas dan stroke dalam 3 hari!"),
    ("hoaks", "Bongkar rekaman live TikTok: server penghitungan suara pilkada di {kota} telah diretas agen asing dan suara dialihkan 100 persen!"),
    ("hoaks", "Beredar reels Instagram di {kota}: klaim uang koin logam seribu rupiah kelapa sawit dibeli bank sentral seharga seratus juta rupiah per keping!"),
    ("hoaks", "Informasi viral TikTok di {kota}: mulai besok seluruh SPBU Pertamina ditutup total selama sebulan karena pasokan BBM langka!"),

    # FAKTA
    ("fakta", "Presiden meresmikan proyek jembatan layang dan jalan tol baru di kawasan {kota} guna memperlancar arus distribusi logistik antardaerah."),
    ("fakta", "BMKG Stasiun Geofisika merilis informasi gempa tektonik berkekuatan magnitudo 4.9 di perairan dekat {kota} dan memastikan tidak berpotensi tsunami."),
    ("fakta", "Aparat Polrestabes {kota} berhasil membekuk sindikat komplotan pencurian kendaraan bermotor dan mengamankan belasan barang bukti."),
    ("fakta", "Wali Kota {kota} meninjau langsung kesiapan sistem drainase kota dan stasiun pompa air untuk mengantisipasi potensi genangan banjir."),
    ("fakta", "Dinas Kesehatan {kota} mengimbau seluruh masyarakat untuk melengkapi imunisasi dasar balita di puskesmas dan posyandu terdekat."),
    ("fakta", "Bank Indonesia perwakilan {kota} bersama Tim Pengendalian Inflasi Daerah (TPID) menggelar operasi pasar murah beras dan minyak goreng curah."),
    ("fakta", "Tim SAR gabungan berhasil menemukan dan mengevakuasi seluruh nelayan yang sempat hilang kontak akibat cuaca buruk di perairan {kota}."),
    ("fakta", "Pemerintah Provinsi mengalokasikan anggaran perbaikan sarana jalan protokol dan fasilitas umum di pusat kota {kota}."),
    ("fakta", "Badan Penanggulangan Bencana Daerah (BPBD) menyalurkan bantuan logistik, tenda darurat, dan makanan siap saji bagi korban bencana di {kota}."),
    ("fakta", "Pertandingan cabang olahraga kejuaraan regional di stadion utama {kota} berlangsung meriah dan ditutup dengan kemenangan atlet tuan rumah."),

    # NEGATIF
    ("negatif", "Aparat keparat di {kota} malah membekingi penjahat! Ayo serbu dan bakar markas mereka malam ini juga, jangan beri mereka ampun!"),
    ("negatif", "Rezim bangsat! Saatnya rakyat di {kota} angkat senjata, bawa bom molotov dan gulingkan penguasa zalim ini sampai titik darah penghabisan!"),
    ("negatif", "Usir semua etnis pendatang parasit itu dari tanah {kota}! Bakar rumah dan hancurkan toko-toko mereka sampai rata dengan tanah!"),
    ("negatif", "Malam ini sweeping seluruh jalur protokol di {kota}, bawa celurit dan sajam, habisi siapa saja kelompok musuh yang berani lewat!"),
    ("negatif", "Kepung dan bakar kantor dinas di {kota} sekarang juga! Bikin kota ini lumpuh total oleh amuk massa malam ini!")
]

def seed_national_threats():
    print("[+] Memulai Seeding Sebaran Nasional Sabang sampai Merauke...")
    
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            # Ambil kota-kota perwakilan dari seluruh 38 provinsi di gazetteer
            cur.execute("""
                SELECT DISTINCT ON (provinsi, nama) nama, provinsi, lat, lon
                FROM gazetteer
                ORDER BY provinsi, nama;
            """)
            all_cities = cur.fetchall()
            print(f"[✓] Berhasil memuat {len(all_cities)} kota dari database gazetteer.")

            # Hapus data social_threats lama agar peta bersih dan tertata
            cur.execute("TRUNCATE social_threats RESTART IDENTITY;")
            print("[✓] Tabel social_threats dibersihkan (TRUNCATE).")

            # 1. Hub Hotspot dengan bobot ancaman tinggi untuk grafik Wilayah Terpanas
            hotspot_weights = {
                "jakarta pusat": 12,
                "surabaya": 9,
                "bandung": 8,
                "medan": 7,
                "makassar": 6,
                "semarang": 5,
                "palembang": 4,
                "denpasar": 4,
            }

            city_map = {c[0].lower(): c for c in all_cities}
            target_items = []

            # Tambahkan data tayang untuk hotspot utama
            for hub_name, count in hotspot_weights.items():
                city_data = city_map.get(hub_name)
                if not city_data:
                    # Cari parsial jika tidak exact match
                    for k, v in city_map.items():
                        if hub_name in k:
                            city_data = v
                            break
                if city_data:
                    for _ in range(count):
                        target_items.append((city_data, True))

            # Tambahkan 85 kota perwakilan lainnya dari berbagai pulau (Tayang)
            other_cities = [c for c in all_cities if c[0].lower() not in hotspot_weights]
            random.seed(2026)
            random.shuffle(other_cities)

            for c in other_cities[:85]:
                target_items.append((c, True))

            # Tambahkan 130 data cadangan untuk Simulator (is_tayang = False)
            for c in (other_cities[85:180] + other_cities[:35]):
                target_items.append((c, False))

            random.shuffle(target_items)
            print(f"[✓] Menyiapkan {len(target_items)} ancaman siber sosial berbobot...")

            total_created = 0
            tayang_count = 0

            for idx, ((nama_kota, prov, lat, lon), is_tayang) in enumerate(target_items):
                kota_display = nama_kota.title()
                kota_slug = nama_kota.lower().replace(" ", "-")
                
                # Pilih template
                cat, tmpl = random.choice(TEMPLATES)
                text = tmpl.format(kota=kota_display, slug=kota_slug)
                
                # Prediksi skor melalui Hybrid AI
                pred = predict_threat(text)
                label = pred["label"]
                prob = pred["probabilitas"]

                if is_tayang:
                    tayang_count += 1

                clean_search = text[:60].replace('"', '').strip()
                if label == "fakta":
                    sumber = "berita"
                    url = f"https://news.google.com/search?q={urllib.parse.quote(clean_search)}&hl=id&gl=ID&ceid=ID:id"
                else:
                    # Distribusi realistis ke platform media sosial populer Indonesia
                    soc_channels = ["tiktok", "twitter", "instagram", "tiktok", "twitter"]
                    sumber = soc_channels[idx % len(soc_channels)]
                    if label == 'hoaks':
                        url = f"https://turnbackhoax.id/?s={urllib.parse.quote(clean_search)}"
                    elif sumber == 'tiktok':
                        url = f"https://www.tiktok.com/search?q={urllib.parse.quote(clean_search)}"
                    elif sumber == 'instagram':
                        first_tag = clean_search.split()[0].lower() if clean_search else "berita"
                        url = f"https://www.instagram.com/explore/tags/{urllib.parse.quote(first_tag)}/"
                    else:
                        url = f"https://twitter.com/search?q={urllib.parse.quote(clean_search)}&f=live"

                # Distribusikan tanggal dibuat_pada: separuh Kemarin (Nov 6) dan separuh Hari Ini (Nov 7)
                days_offset = idx % 2
                hour_offset = (idx * 3) % 24
                minute_offset = (idx * 17) % 60

                query = """
                    INSERT INTO social_threats 
                    (sumber, teks, label, probabilitas, geom, lokasi_nama, url, status_tayang, dikoreksi, dibuat_pada)
                    VALUES 
                    (%s, %s, %s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326), %s, %s, TRUE, FALSE, 
                     CURRENT_DATE - (%s || ' days')::interval + (%s || ' hours')::interval + (%s || ' minutes')::interval);
                """
                cur.execute(query, (
                    sumber,
                    text,
                    label,
                    prob,
                    lon,
                    lat,
                    f"{kota_display} ({prov})",
                    url,
                    days_offset,
                    hour_offset,
                    minute_offset
                ))
                total_created += 1

            conn.commit()

            # Verifikasi sebaran
            cur.execute("SELECT count(*), count(*) FILTER (WHERE status_tayang = TRUE) FROM social_threats;")
            tot, tay = cur.fetchone()
            
            cur.execute("""
                SELECT split_part(lokasi_nama, '(', 2) AS prov, count(*) 
                FROM social_threats 
                WHERE status_tayang = TRUE 
                GROUP BY prov 
                ORDER BY count(*) DESC;
            """)
            prov_stats = cur.fetchall()

            print(f"\n=======================================================")
            print(f"  🎉 SEEDING SEBARAN NASIONAL SELESAI!")
            print(f"  📊 Total Ancaman di Database : {tot} titik")
            print(f"  🔴 Titik Langsung TAYANG Peta: {tay} titik")
            print(f"  ⏳ Titik Cadangan Simulator : {tot - tay} titik")
            print(f"  📍 Jumlah Provinsi Terwakili : {len(prov_stats)} provinsi")
            print(f"=======================================================")

if __name__ == "__main__":
    seed_national_threats()
