# GeoSentry

GeoSentry adalah dasbor WebGIS untuk mencari dan memantau berita serta postingan publik. Sistem menggabungkan pengambilan data dari feed dan platform publik, pencarian sumber terkait, prediksi klasifikasi AI, serta peta berbasis Leaflet dan PostgreSQL/PostGIS.

## Menjalankan aplikasi

Sebelum memulai, siapkan PostgreSQL dengan ekstensi PostGIS, database GeoSentry, tabel dari [`data/schema.sql`](data/schema.sql), data wilayah di tabel `gazetteer`, Node.js, dan lingkungan Python proyek di `.venv`. Backend memerlukan konfigurasi koneksi database pada `.env` (`DATABASE_URL`) dan Python pada `.venv/bin/python3`.

Jalankan dari folder proyek:

```bash
./run.sh
```

Atau jalankan backend secara langsung:

```bash
cd backend
npm start
```

Setelah backend siap, buka [http://localhost:3000](http://localhost:3000). Server menyiapkan kolom verifikasi database, lalu memulai pengambilan data awal. Pastikan PostgreSQL/PostGIS, model `models/sgd_model.pkl`, dan dependensi Python proyek tersedia. [`backend/package.json`](backend/package.json) berisi dependensi Node.js.

## Alur data

1. Backend menjalankan pengambilan berita dan postingan sosial saat mulai, lalu mengulanginya setiap satu jam selama server hidup.
2. Pengambil berita membaca feed TurnBackHoax, CekFakta, Google News, Antara, CNBC Indonesia, dan pencarian berita Google News untuk 38 provinsi. Tautan agregator diselesaikan ke situs penerbit jika memungkinkan.
3. Pengambil sosial memeriksa Mastodon, Bluesky, Reddit, X, TikTok, dan Instagram menggunakan akses publik. Rincian cara akses dan keterbatasannya ada di [dokumentasi arsitektur](DOKUMEN_ARSITEKTUR_FINAL.md).
4. Sistem membaca teks yang berhasil diperoleh, mencari artikel atau postingan yang berhubungan, menjalankan prediksi model, lalu menerapkan aturan bukti sumber. Data yang lolos disimpan di `social_threats`; referensi dan dasar klasifikasi ikut disimpan bila tersedia.
5. Berita atau postingan baru dikirim ke browser melalui Socket.IO. Peta hanya menampilkan data berumur maksimal 24 jam dengan label `fakta` atau `hoaks` dan status `terverifikasi` atau `prediksi_sementara`.

> Pengambilan berjalan berkala, tetapi jumlah data tidak dijamin. Feed dapat kosong, platform dapat membatasi akses, postingan mungkin tidak terindeks, dan sumber bisa gagal diambil. Sistem juga tidak menjamin teks lengkap tersedia untuk semua jenis postingan.

## Cara membaca label

- `Fakta` berwarna biru dan `Hoaks` berwarna merah. Warna menjelaskan label prediksi, bukan tingkat kepastian.
- Status `terverifikasi` berarti aturan sistem menemukan dasar sumber yang memenuhi kriteria internal. Status `prediksi_sementara` berarti label belum menjadi putusan yang kuat. Keduanya tetap hasil otomatis dan perlu diperiksa terhadap sumber asli.
- Model lokal memakai `HashingVectorizer` dan `SGDClassifier`, ditambah aturan pola teks. Model dapat salah; kemiripan teks atau skor kecocokan sumber saja tidak membuktikan sebuah klaim benar atau salah.
- Model internal masih memiliki kelas `negatif` untuk sentimen/ajakan berbahaya. Peta utama dan statistik dashboard membatasi tampilan pada `fakta` dan `hoaks`; fitur analisis dapat tetap mengembalikan label negatif.
- Analisis teks mandiri mencari sumber terkait setelah prediksi awal. Jika bukti tidak cukup, hasil tetap sementara dengan keyakinan dibatasi. Hasil hanya disimpan sebagai titik peta bila memenuhi status verifikasi dan teks menyebut lokasi yang dapat dikenali.

## Data dan masa simpan

- Jendela peta dan statistik adalah 24 jam terakhir.
- Pembersihan otomatis berjalan setiap lima menit saat backend hidup. Data yang lebih lama dari 24 jam disembunyikan dari peta dengan `status_tayang = FALSE`.
- Data dihapus permanen setelah masa simpan yang diatur oleh `DATA_RETENTION_DAYS` di `.env`. Nilai bawaan: 90 hari.
- Tabel `gazetteer` berisi nama dan koordinat wilayah dan dipakai untuk geocoding serta direktori provinsi. Data ini tidak ikut dibersihkan bersama berita.

## Fitur antarmuka

- Peta titik radar, dengan tombol untuk menampilkan atau menyembunyikan marker.
- Filter label fakta/hoaks dan provinsi, pencarian berita, daftar feed, serta direktori provinsi.
- Formulir analisis AI yang menampilkan prediksi, dasar analisis, tingkat kecocokan, dan tautan sumber terkait bila ditemukan.
- Mode terang dan gelap. Basemap OpenStreetMap tetap terang pada kedua mode.
- Koreksi label oleh analis dari popup peta; koreksi disimpan di database dan dikirim ke model melalui pembelajaran inkremental.

## Endpoint backend utama

| Metode dan endpoint | Fungsi |
| --- | --- |
| `GET /api/threats` | Data titik peta dalam GeoJSON |
| `GET /api/stats` | Statistik data aktif 24 jam |
| `GET /api/provinces` | Ringkasan sebaran per provinsi |
| `GET /api/cities` | Daftar wilayah dari `gazetteer` |
| `GET /api/ingest/status` | Status pengambilan berita dan sosial terakhir |
| `GET /api/search/news?q=...` | Mencari berita/postingan dan sumber terkait |
| `POST /api/analyze` | Menganalisis teks dan mencari sumber terkait |
| `POST /api/threats/:id/correct` | Mencatat koreksi label analis |
| `POST /api/viral/sync` | Menjalankan sinkronisasi berita secara manual |
| `POST /api/social/sync` | Menjalankan sinkronisasi sosial secara manual |
| `POST /api/social/inspect-url` | Memeriksa URL sosial publik secara manual |

## Peta file dan status skrip

### Dipakai oleh aplikasi aktif

- `backend/server.js` — API, Socket.IO, jadwal pengambilan, migrasi kolom verifikasi, dan pembersihan data.
- `frontend/` — halaman, tampilan, peta, pencarian, dan analisis.
- `scripts/scraper/scrape_viral_daily.py` dan `scripts/scraper/free_social_scraper.py` — worker berita dan sosial per jam.
- `scripts/scraper/ytdlp_social_scraper.py` — pemeriksaan URL sosial melalui endpoint manual.
- `scripts/search_news.py` dan `scripts/tools/verification_gate.py` — pencarian sumber, pencocokan isi, dan gerbang bukti.
- `scripts/nlp/` dan `models/sgd_model.pkl` — klasifikasi lokal dan pembaruan model saat ada koreksi.
- `scripts/gazetteer/geocode.py` — pencocokan nama lokasi saat ingest dan analisis.

### Utilitas manual atau jalur lama

File-file berikut tidak dipanggil oleh worker aplikasi aktif. Pertahankan sampai dipastikan tidak lagi dibutuhkan; jangan menganggapnya bagian dari pengambilan otomatis:

- `scripts/gazetteer/load_gazetteer.py` — pengisian awal data wilayah.
- `scripts/nlp/generate_dataset.py` dan `scripts/nlp/train.py` — pembuatan data latihan dan pelatihan model secara manual.
- `scripts/tools/reclassify_active.py` — utilitas pemeliharaan klasifikasi.
- `scripts/scraper/rss_scraper.py` dan `scripts/scraper/twitter_import.py` — jalur scraper/impor lama yang tidak dipakai server saat ini.
- `scripts/simulator.py` — simulator buffer lama. Endpoint `/api/simulator/*` yang dibutuhkan mode langkah/simulasi tidak tersedia pada server saat ini; mode reset juga dapat mengubah `status_tayang` data secara langsung.
- `scripts/scraper/seed_national_threats.py` — seeder demo manual. Skrip ini menjalankan `TRUNCATE social_threats`; menjalankannya akan menghapus seluruh isi tabel berita/postingan.
- `docs/research-hoax-platforms.md` — catatan riset historis, bukan gambaran perilaku aplikasi saat ini.

Bagian ini adalah catatan status pemakaian, bukan instruksi untuk menghapus file. Tinjau pemanggil dan riwayat penggunaan sebelum mengarsipkan atau menghapus utilitas apa pun.
