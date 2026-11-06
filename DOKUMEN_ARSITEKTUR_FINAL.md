# DOKUMEN ARSITEKTUR TEKNIS & PROPOSAL AKHIR (FINAL DOSSIER)
**Nama Proyek:** GeoSentry: Dasbor WebGIS Pemantauan Disinformasi dan Ancaman Siber Sosial Berbasis Incremental Machine Learning  
**Kategori Lomba:** Kategori 04 — Siber Sosial / Cognitive Domain  
**Status Implementasi:** 100% Selesai (Sprint MVP 20 Hari Terverifikasi)  
**Tanggal Rilis:** 2026-11-06  

---

## 1. Ringkasan Eksekutif (Executive Summary)

GeoSentry adalah platform intelijen sumber terbuka (*Open Source Intelligence* / OSINT) berbasis WebGIS spasial yang dirancang untuk mendeteksi, mengklasifikasi, dan memetakan eskalasi narasi disinformasi, propaganda siber, dan anomali kebisingan opini publik di wilayah Indonesia.

Sistem ini memecahkan tiga kendala utama sistem OSINT konvensional:
1. **Biaya Operasional Token:** Mengadopsi prinsip *Zero-Token Policy* dengan menggunakan algoritma *Incremental Machine Learning* lokal (`SGDClassifier` + `HashingVectorizer`), tanpa bergantung pada API LLM komersial berbayar.
2. **Ketergantungan API Eksternal & Batasan Rate Limit:** Menggunakan mesin geocoding kamus wilayah (*offline gazetteer*) 500 kabupaten/kota di Indonesia untuk mengubah penyebutan nama lokasi menjadi koordinat PostGIS tanpa kuota layanan eksternal.
3. **Risiko Kegagalan Demo Langsung (*Pitch Presentation Failure*):** Menerapkan strategi *Buffer Database Simulation* dengan kontrol siaran WebSockets real-time yang menjamin demonstrasi visual peta di hadapan dewan juri berjalan mulus tanpa risiko internet lambat atau pemblokiran API Twitter.

---

## 2. Arsitektur Sistem (System Architecture)

```text
  +-------------------------------------------------------------------------------+
  |                             DATA SOURCES (JALUR GANDA)                        |
  +---------------------------------------+---------------------------------------+
                                          |
        [JALUR 1: OPINI PUBLIK]           |          [JALUR 2: FAKTA & VALIDASI]
         Twitter/X Pre-Buffer             |          Portal Berita & TurnBackHoax
        (scripts/scraper/twitter_import)  |          (scripts/scraper/rss_scraper)
                                          |
                                          v
  +-------------------------------------------------------------------------------+
  |                        LOCAL AI ENGINE & GEOCODING                            |
  |  - Incremental SGDClassifier (Loss: log_loss, Penalty: L2)                    |
  |  - Stateless HashingVectorizer (n_features: 2^18, ngrams: 1-2)                |
  |  - Offline Gazetteer Geocoder (500 Kabupaten/Kota se-Indonesia)               |
  +---------------------------------------+---------------------------------------+
                                          |
                                          v
  +-------------------------------------------------------------------------------+
  |                   POSTGRESQL 18 + POSTGIS 3.6 DATABASE                        |
  |  - Tabel 'gazetteer'       : 500 entitas koordinat spasial                    |
  |  - Tabel 'social_threats'  : 190 baris buffer (geom Point SRID 4326)          |
  |  - Indeks GIST             : Pencarian & filter spasial ultra-cepat           |
  +---------------------------------------+---------------------------------------+
                                          |
                                          v
  +-------------------------------------------------------------------------------+
  |                     BACKEND & REAL-TIME WEBSOCKETS (Node.js)                  |
  |  - Express REST API (/api/threats, /api/stats, /api/simulator/*)              |
  |  - Native PostGIS GeoJSON Serializer (ST_AsGeoJSON)                           |
  |  - Socket.io Broadcast Engine                                                 |
  |  - Feedback Loop Controller (Trigger partial_fit Python secara otomatis)       |
  +---------------------------------------+---------------------------------------+
                                          |
                                          v
  +-------------------------------------------------------------------------------+
  |                     FRONTEND WEBGIS DASBOR (Leaflet + Tailwind)               |
  |  - Leaflet.js Basemap CartoDB Dark Matter                                     |
  |  - Lapisan Ganda: Marker Radar Berdenyut + Dynamic Heatmap (Leaflet.heat)     |
  |  - Analitik Chart.js: Donut Distribusi Isu & Bar Wilayah Terpanas             |
  |  - Tombol Feedback Loop: Koreksi Analis Langsung dari Peta                    |
  |  - Floating Simulator Deck: Mulai/Jeda/Injeksi Aliran Data Presentasi          |
  +-------------------------------------------------------------------------------+
```

---

## 3. Spesifikasi Komponen & Modul Teknis

### A. Modul Kecerdasan Buatan (NLP & Incremental Learning)
- **File:** `scripts/nlp/train.py` & `scripts/nlp/predict.py`
- **Algoritma:** `SGDClassifier` dengan fungsi kerugian `loss='log_loss'` (regresi logistik stokastik).
- **Vektorisasi:** `HashingVectorizer(n_features=262144, alternate_sign=False, ngram_range=(1,2))`
  - *Stateless:* Tidak memakan alokasi memori RAM untuk menyimpan kamus kosa kata (*vocabulary dictionary*).
  - *Lightweight:* Mampu berjalan di laptop spesifikasi standar dengan konsumsi RAM < 150 MB.
- **Pembersihan Bahasa:** Mengintegrasikan pustaka sastrawi stopword remover dan pembersih karakter liar.
- **Feedback Loop (Online Active Learning):**
  - Ketika analis menekan tombol koreksi di antarmuka WebGIS, endpoint `/api/threats/:id/correct` memicu perintah Python:
    ```bash
    python -m scripts.nlp.predict --retrain "<teks>" "<label_koreksi>"
    ```
  - Metode `partial_fit()` dieksekusi secara instan dan bobot model pada `models/sgd_model.pkl` diperbarui detik itu juga tanpa perlu melatih ulang dari awal (*no full re-training required*).

### B. Modul Geocoding Offline & Spasial PostGIS
- **File:** `scripts/gazetteer/load_gazetteer.py` & `scripts/gazetteer/geocode.py`
- **Kamus Wilayah (*Gazetteer*):** 500 kabupaten/kota di 38 provinsi Indonesia beserta koordinat lintang & bujur akurat.
- **Algoritma Pencocokan:**
  - *Longest Match First:* Nama wilayah terpanjang dicocokkan terlebih dahulu (misal: "Jakarta Selatan" sebelum "Jakarta", "Kutai Kartanegara" sebelum "Kutai") untuk mencegah tabrakan substring (*substring collision*).
  - *Word Boundary Regex:* Mengisolasi kata kunci agar tidak mencocokkan kata sembarang (misal: "solo" tidak tertukar dengan "resolusi").
  - Menangani variasi sebutan umum seperti awalan "kota", "kabupaten", "kab.".
- **Penyimpanan Spasial:** Disimpan dalam tipe data `GEOMETRY(Point, 4326)` dengan indeks GIST (`idx_threats_geom`), diakses native melalui query `ST_AsGeoJSON(geom)`.

### C. Backend & Mesin WebSockets
- **File:** `backend/server.js`
- **Kerangka Kerja:** Node.js v26 + Express.js + Socket.io + `pg` (PostgreSQL Client).
- **Endpoint Utama:**
  - `GET /api/threats` — Mengembalikan seluruh ancaman aktif (`status_tayang = TRUE`) dalam format standar *GeoJSON FeatureCollection*.
  - `GET /api/stats` — Mengembalikan ringkasan statistik metrik, proporsi kategori, dan 5 wilayah teratas.
  - `POST /api/threats/:id/correct` — Menjalankan feedback loop koreksi analis, memperbarui database, melatih model AI, dan menyiarkan perubahan ke seluruh peramban yang terhubung.
  - `POST /api/simulator/step` — Mengaktifkan 1 baris antrean buffer dan memancarkan event `new_threat` via WebSockets.
  - `POST /api/simulator/start` & `/api/simulator/stop` — Menjalankan timer berkala otomatis (default interval 2.5 detik).
  - `POST /api/simulator/reset` — Mengembalikan semua status tayang menjadi `FALSE`.

### D. Frontend WebGIS Dasbor
- **File:** `frontend/index.html`, `frontend/css/style.css`, `frontend/js/app.js`
- **Pustaka:** Leaflet.js, Leaflet.heat, Chart.js, Tailwind CSS, Lucide Icons.
- **Fitur Unggulan Antarmuka:**
  - *Radar Pulsing Markers:* Titik lokasi memancarkan animasi radar berdenyut sesuai klasifikasi (Merah = Hoaks, Biru = Fakta, Amber = Negatif).
  - *Dynamic Heatmap:* Lapisan konsentrasi ancaman untuk melihat densitas geografis isu panas.
  - *Interactive Popup Feedback:* Analis dapat melihat skor keyakinan AI dan mengoreksi prediksi dengan satu klik.
  - *Live Ticker Feed:* Aliran data baru masuk secara otomatis tanpa *refresh* halaman.

---

## 4. Strategi Presentasi Lomba: Simulator Buffer Anti-Gagal

Untuk menjamin presentasi 100% mulus di hadapan dewan juri tanpa kendala koneksi internet lambat atau *rate-limiting* media sosial:

1. **Pra-Presentasi:**
   - 190 baris data opini publik dan fakta tersimpan di database dengan kolom `status_tayang = FALSE`.
   - Layar peta Leaflet di proyektor bersih dan siap memantau.
2. **Saat Presentasi Dimulai:**
   - Tekan tombol **"Mulai Simulasi"** pada panel bawah dasbor (atau jalankan `python scripts/simulator.py --interval 2.5`).
   - Setiap 2.5 detik, baris baru diaktifkan (`status_tayang = TRUE`).
   - Backend memancarkan data GeoJSON baru via WebSocket ke browser juri.
3. **Efek Visual:**
   - Dewan juri melihat peta Indonesia secara dinamis hidup: titik-titik radar dan gradien panas bermunculan satu per satu disertai pergerakan grafik statistik dan feed radar langsung.
4. **Demonstrasi Feedback Loop:**
   - Presenter mengklik salah satu marker hoaks di peta, menekan tombol **"Koreksi ke Fakta"**.
   - Sistem seketika mengubah warna marker menjadi biru, memperbarui grafik, dan melatih ulang model AI di latar belakang detik itu juga.

---

## 5. Panduan Menjalankan Sistem

1. **Memulai Server WebGIS:**
   ```bash
   cd /home/gry/Gerry/GeoSentry/backend
   npm start
   ```
   Akses aplikasi di peramban: `http://localhost:3000`

2. **Menjalankan Simulator (via CLI atau UI):**
   ```bash
   cd /home/gry/Gerry/GeoSentry
   source .venv/bin/activate
   python scripts/simulator.py --interval 2.5
   ```

3. **Mereset Kondisi Awal:**
   ```bash
   python scripts/simulator.py --reset
   ```
