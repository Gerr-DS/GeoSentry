# 🛡️ GeoSentry: Dasbor WebGIS Intelijen Ancaman Siber Sosial

Platform pemantauan disinformasi dan ancaman siber sosial (Kategori 04 — Cognitive Domain) berbasis **Incremental Machine Learning** (`SGDClassifier`) dan **WebGIS Leaflet.js** tanpa ketergantungan API berbayar (*Zero-Token Policy*). Dilengkapi dengan arsitektur **Buffer Simulasi Real-Time** anti-gagal untuk presentasi lomba.

---

## 🚀 Panduan Menjalankan Sistem

### 1. Menjalankan Backend & WebGIS Dashboard
```bash
cd /home/gry/Gerry/GeoSentry/backend
npm start
```
Buka peramban (browser) di: **[http://localhost:3000](http://localhost:3000)**

---

### 2. Menjalankan Simulator Presentasi (Aliran Data Real-Time)

Jalankan script simulator di terminal terpisah saat presentasi dimulai:
```bash
cd /home/gry/Gerry/GeoSentry
source .venv/bin/activate
python scripts/simulator.py --interval 2.5
```
*Atau gunakan tombol **"Mulai Simulasi"** langsung dari antarmuka WebGIS!*

#### Opsi Perintah Simulator:
- Injeksi 1 data manual:
  ```bash
  python scripts/simulator.py --step
  ```
- Cek status antrean buffer:
  ```bash
  python scripts/simulator.py --status
  ```
- Reset buffer ke kondisi awal (0 tayang):
  ```bash
  python scripts/simulator.py --reset
  ```

---

## 🧠 Fitur Inti yang Telah Dibangun

1. **Dual-Data Pipeline**:
   - Jalur Opini Publik: Buffer cuitan Twitter/X (`scripts/scraper/twitter_import.py`).
   - Jalur Fakta & Validasi: Penarik RSS berita & portal cek fakta (`scripts/scraper/rss_scraper.py`).
2. **Offline Geocoding**:
   - Pencocokan entitas 500 kabupaten/kota di seluruh Indonesia ke titik koordinat PostGIS (`scripts/gazetteer/geocode.py`).
3. **Local Incremental Learning (Zero-Token)**:
   - Stateless `HashingVectorizer(n_features=2^18)` + `SGDClassifier(loss='log_loss')`.
   - Menghasilkan label (`hoaks`, `fakta`, `negatif`) beserta skor probabilitas keyakinan.
4. **Interactive Feedback Loop**:
   - Analis dapat mengoreksi hasil klasifikasi langsung dari popup peta Leaflet.
   - Mengoreksi data memicu `partial_fit()` pada model AI secara instan detik itu juga.
5. **WebGIS Leaflet.js & Visualisasi Ganda**:
   - Lapisan titik marker berdenyut (Merah: Hoaks, Biru: Fakta, Amber: Sentimen Negatif).
   - Lapisan Peta Panas (*Heatmap*) dinamis (`leaflet.heat`).
   - Panel analitik statistik Donut Chart & Bar Chart wilayah terpanas (`Chart.js`).
