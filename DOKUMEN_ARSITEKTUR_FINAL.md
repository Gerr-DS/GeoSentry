# Dokumentasi Arsitektur GeoSentry

Dokumen ini menggambarkan alur yang dijalankan oleh kode GeoSentry saat ini. Ini bukan klaim bahwa semua platform selalu dapat diakses atau bahwa prediksi AI pasti benar. Endpoint, sumber, dan jadwal di bawah merujuk pada `backend/server.js` serta skrip yang dipanggilnya.

## Ringkasan

GeoSentry adalah aplikasi WebGIS yang mengumpulkan berita dan postingan publik, membandingkan teks dengan sumber terkait, menghasilkan prediksi label, lalu menyajikan data yang lolos gerbang verifikasi di peta. Komponen utamanya adalah:

- **Antarmuka:** HTML, CSS, JavaScript, Leaflet, Chart.js, Socket.IO client.
- **Backend:** Node.js, Express, Socket.IO.
- **Penyimpanan spasial:** PostgreSQL dan PostGIS.
- **Pengambilan serta analisis:** Python, feed RSS publik, endpoint/API publik, pembacaan halaman artikel, dan model NLP lokal.
- **Model lokal:** `HashingVectorizer` dan `SGDClassifier` dengan aturan teks tambahan; model dibaca dari `models/sgd_model.pkl`.

```text
Feed berita ─────────┐
Platform sosial ─────┼─> Pengambil data ─> isi teks + URL asli
Teks/URL dari pengguna┘                         │
                                                v
                                    pencarian sumber terkait
                                    + prediksi NLP + gerbang bukti
                                                │
                                                v
                                   PostgreSQL / PostGIS
                                                │
                                  REST API + Socket.IO
                                                │
                                                v
                                  Dashboard Leaflet WebGIS
```

## Pengambilan data otomatis

### Jadwal worker

Setelah koneksi dan skema verifikasi database siap, backend membuka server HTTP, lalu menjalankan pengambilan awal setelah jeda sekitar tiga detik. Worker berikutnya dijadwalkan setiap 60 menit selama proses backend tetap hidup. Pada setiap putaran, worker mengambil berita terlebih dahulu lalu media sosial. Tombol sinkronisasi manual juga tersedia melalui endpoint API.

Pembersihan data dijadwalkan setiap lima menit. Pemeriksaan pergantian tanggal pukul 00.00 WIB berjalan terpisah setiap 30 detik dan memicu pengambilan berita portal. Jadwal tersebut adalah upaya berkala, bukan jaminan data baru akan selalu ditemukan setiap jam.

### Portal berita dan pencarian provinsi

[`scripts/scraper/scrape_viral_daily.py`](scripts/scraper/scrape_viral_daily.py) membaca feed berikut:

- TurnBackHoax dan CekFakta.
- Google News Indonesia.
- Antara News dan CNBC Indonesia.
- Feed pencarian Google News untuk 38 provinsi.

Untuk feed berita, item yang bertanggal lebih lama dari 24 jam dilewati. Jika feed umum tidak menyertakan tanggal, skrip dapat memperlakukannya sebagai item baru; feed CekFakta diwajibkan memiliki tanggal terbit yang dapat dibaca. Google News sering membungkus tautan artikel: skrip mencoba membuka tautan penerbit aslinya sebelum menyimpan.

Data sumber yang berhasil diambil kemudian diperiksa lagi oleh `scripts/tools/verification_gate.py`. Sistem mencoba membaca halaman artikel, mencocokkan teks sumber dengan kandidat, dan memeriksa apakah ada putusan cek fakta yang eksplisit.

### Platform sosial

[`scripts/scraper/free_social_scraper.py`](scripts/scraper/free_social_scraper.py) menggabungkan beberapa cara akses publik:

| Platform | Cara pengambilan saat ini | Batas yang perlu diketahui |
| --- | --- | --- |
| Mastodon | Timeline dan pencarian publik pada instance yang dikonfigurasi, termasuk `mastodon.social` dan instance lokal. | Ketersediaan serta postingan yang terlihat bergantung pada instance. |
| Bluesky | Topik tren publik Bluesky digunakan untuk mencari postingan melalui endpoint publik Bluesky. | Hasil tergantung endpoint publik, topik yang ditemukan, dan pembatasan layanan. |
| Reddit | Listing publik dari sejumlah subreddit Indonesia dan kategori terkait. | Tidak menjamin seluruh subreddit atau postingan terbaru bisa diakses. |
| X/Twitter | Topik tren Indonesia dibaca dari Trends24, kemudian Bing RSS dipakai untuk menemukan URL postingan publik X. RSSHub tidak dipakai oleh jalur ini. | Bing hanya memuat konten yang terindeks; cuplikan yang diperoleh tidak selalu sama dengan isi lengkap postingan. |
| Instagram | Bing RSS mencari URL postingan atau reel publik berdasarkan topik tren. | Akses publik/indeks tidak menjamin isi caption lengkap atau seluruh postingan dapat dibaca. |
| TikTok | Bing RSS mencari video publik berdasarkan topik; sistem mencoba TikTok oEmbed dan metadata `yt-dlp` untuk URL video langsung. | Metadata tidak sama dengan transkrip video; pengambilan teks dan waktu unggah dapat gagal. |

Variabel `GEOSENTRY_RSSHUB_BASES` yang masih tercantum di `.env.example` adalah pengaturan lama; kode pengambil sosial saat ini tidak membacanya.

Item sosial yang diproses harus memiliki URL langsung dan waktu unggah yang diketahui dalam rentang 24 jam. Konten Mastodon, Bluesky, dan Reddit diambil dari respons publiknya. Untuk X dan Instagram, pencarian mengandalkan cuplikan indeks publik; TikTok dapat melengkapi metadata, tetapi tidak otomatis mentranskripsikan suara video. Karena itu, isi lengkap tidak selalu tersedia untuk setiap postingan.

[`scripts/scraper/ytdlp_social_scraper.py`](scripts/scraper/ytdlp_social_scraper.py) dipanggil untuk pemeriksaan URL sosial tunggal melalui endpoint manual; skrip ini bukan worker otomatis per jam.

## Pencarian dan analisis berita

### Pencarian kata kunci

Browser mengirim kata kunci ke `GET /api/search/news?q=...`. Backend memberikan proses pencarian ke [`scripts/search_news.py`](scripts/search_news.py) dan menyertakan postingan sosial relevan yang sudah tersimpan serta berstatus terverifikasi. Skrip pencarian:

1. Membersihkan teks panjang menjadi kueri pencarian.
2. Memperluas sejumlah padanan umum bahasa Indonesia, misalnya `mundur` dan `mengundurkan diri`.
3. Mencari feed berita Bing News, menyelesaikan tautan agregator bila memungkinkan, lalu membaca isi halaman penerbit.
4. Mengukur kecocokan kueri terhadap judul dan isi. Isi artikel ikut dianalisis; hasil tidak dipilih berdasarkan judul saja.
5. Menampilkan tautan sumber yang ditemukan, tipe konten, isi yang dianalisis, label prediksi, dan kecocokan.

Peta kecocokan membantu menyaring hasil pencarian, tetapi bukan ukuran kebenaran. Sebuah artikel yang membahas topik sama belum tentu membuktikan klaim yang dicari.

### Analisis teks mandiri

`POST /api/analyze` menjalankan urutan berikut:

1. Model NLP lokal membuat prediksi awal berdasarkan teks yang dimasukkan.
2. Sistem mencoba mencari berita dan postingan terkait serta menilai isi dan kecocokannya.
3. Jika pencarian memberi dasar yang cukup, hasil sumber dapat menentukan atau mengubah ringkasan label. Jika tidak cukup, sistem mengembalikan prediksi sementara dengan tingkat keyakinan dibatasi.
4. Sistem hanya menyimpan hasil sebagai titik peta bila statusnya `terverifikasi` dan lokasi dapat dikenali dari teks. Teks tanpa lokasi atau hasil sementara tidak otomatis menjadi titik peta.
5. Jika sumber gagal dicari, aplikasi tetap dapat mengembalikan prediksi teks sementara; tidak boleh dianggap sebagai verifikasi klaim.

Hasil analisis menampilkan sumber terkait yang ditemukan. Bila tautan asli tidak tersedia, antarmuka dapat memberikan tautan pencarian dan menandainya sebagai pencarian, bukan sebagai sumber berita.

## Gerbang verifikasi dan makna label

Gerbang verifikasi berada di [`scripts/tools/verification_gate.py`](scripts/tools/verification_gate.py). Prinsip yang diterapkan berbeda menurut bukti yang tersedia:

- **Putusan cek fakta eksplisit:** label fakta/hoaks dari pemeriksa fakta yang didukung dan cocok dengan isi dapat diberi status terverifikasi.
- **Sumber primer resmi:** halaman resmi yang isinya cocok dengan klaim dapat mendukung label fakta. Ini berarti sistem menemukan laporan/sumber primer yang mendukungnya, tidak otomatis membuktikan setiap implikasi dalam teks.
- **Portal berita tepercaya:** isi artikel yang cukup cocok dapat menghasilkan prediksi fakta sementara. Satu laporan berita bukan putusan cek fakta independen.
- **Bukti tidak cukup atau sumber tidak terbaca:** sistem masih dapat menampilkan prediksi sementara berkeyakinan rendah. Pada jalur fallback umum, labelnya dapat menjadi hoaks sementara. Label ini adalah keluaran aturan fallback, bukan bukti bahwa klaim telah terbukti palsu.
- **Ajakan eksplisit melakukan tindakan berbahaya:** dapat menghasilkan label sentimen `negatif`; sentimen ini bukan putusan benar/salah suatu klaim.
- **Bukti bertentangan:** sistem menurunkan kepastian atau menahan klaim yang tidak memiliki dasar memadai, sesuai jalur pemrosesannya.

> **Penting:** `terverifikasi` adalah status yang ditetapkan aturan aplikasi berdasarkan tipe dan kecocokan sumber yang ditemukan. Itu bukan jaminan hasilnya selalu benar. `prediksi_sementara` secara eksplisit berarti hasil belum diputuskan kuat oleh bukti.

Model lokal menghasilkan tiga kelas internal: `fakta`, `hoaks`, dan `negatif`. Peta utama, filter, dan statistik memilih `fakta` serta `hoaks`; hasil negatif tidak ditampilkan sebagai titik pada peta. Analisis/pencarian dapat tetap menggunakan label negatif untuk menjelaskan sentimen.

## Model dan umpan balik analis

Model dasar dibuat dengan `HashingVectorizer` (262.144 fitur, unigram dan bigram) serta `SGDClassifier` dengan `log_loss`. Teks diproses menggunakan normalisasi dan stopword Bahasa Indonesia. `scripts/nlp/predict.py` menggabungkan probabilitas model dengan sejumlah aturan pola untuk konteks cek fakta, berita, klaim hoaks, dan ajakan berbahaya.

Data model awal berasal dari `data/train.csv`, yang disiapkan lewat utilitas dataset/pelatihan. Dataset mengandung contoh terstruktur dan tidak dengan sendirinya menjadi bukti eksternal atas klaim baru. Skor probabilitas model juga bukan probabilitas kebenaran yang telah dikalibrasi.

Koreksi analis lewat `POST /api/threats/:id/correct` memperbarui label dan dasar koreksi di database. Backend kemudian memanggil `partial_fit()` untuk pembelajaran inkremental di latar belakang. Perubahan label yang ditandai analis tidak berarti klaim tersebut sudah diperiksa oleh pihak independen.

## Data, database, dan masa simpan

Skema dasar ada di [`data/schema.sql`](data/schema.sql):

- `social_threats` menyimpan sumber, teks, label, skor, geometri, lokasi, URL, referensi, status tampil, status/dasar/waktu verifikasi, koreksi, dan waktu data.
- `gazetteer` menyimpan nama wilayah, provinsi, serta koordinat. `scripts/gazetteer/geocode.py` memakai daftar ini untuk mengenali lokasi di teks.
- Geometri titik memakai SRID 4326 dengan indeks GIST.

Saat mulai, backend menambahkan kolom verifikasi bila belum ada dan memasang trigger database agar item berstatus pending atau belum didukung tidak disetel tampil. Tabel `gazetteer` terpisah dari pembersihan berita.

Aturan penyajian dan retensi:

1. Endpoint peta dan statistik mengambil data yang berumur maksimal 24 jam, `status_tayang = TRUE`, memiliki status `terverifikasi` atau `prediksi_sementara`, dan berlabel fakta/hoaks.
2. Setiap lima menit backend menyembunyikan data yang melewati 24 jam dengan mengubah `status_tayang` menjadi `FALSE`.
3. Data berita/postingan dihapus permanen setelah `DATA_RETENTION_DAYS`; bawaan 90 hari. Pengaturan ini berada di `.env`.

Pembersihan tersebut hanya berjalan selama backend hidup.

## Antarmuka dan pembaruan langsung

Frontend memakai Leaflet untuk peta dan OpenStreetMap sebagai basemap. Basemap tetap terang baik pada mode terang maupun gelap. Marker fakta berwarna biru dan hoaks merah. Tombol marker mengaktifkan atau menonaktifkan titik radar; saat ini tidak ada lapisan heatmap.

Socket.IO mengirim event untuk titik baru, perubahan hasil koreksi, sinkronisasi, dan penghapusan dari tampilan. Browser juga dapat memuat ulang data dari REST API. Peta tidak menampilkan lokasi tebakan untuk data yang tidak memiliki lokasi; lokasi nasional yang ditetapkan skrip ingest dapat menggunakan titik representatif Indonesia.

## Endpoint utama

| Endpoint | Keterangan |
| --- | --- |
| `GET /api/threats` | Data peta terbaru dalam GeoJSON |
| `GET /api/stats` | Hitungan aktif dan ringkasan label/sumber |
| `GET /api/provinces` | Ringkasan berita per provinsi |
| `GET /api/cities` | Direktori wilayah dari gazetteer |
| `GET /api/ingest/status` | Status proses berita dan media sosial terakhir |
| `GET /api/viral/info` | Informasi mesin pemantauan |
| `GET /api/search/news?q=...` | Pencarian berita dan postingan terkait |
| `POST /api/analyze` | Analisis teks dan pencarian sumber terkait |
| `POST /api/threats/:id/correct` | Koreksi label oleh analis |
| `POST /api/viral/sync` | Sinkronisasi berita manual |
| `POST /api/social/sync` | Sinkronisasi sosial manual |
| `POST /api/social/inspect-url` | Pemeriksaan URL sosial langsung |

## Skrip dan status pemakaian

### Jalur aplikasi aktif

- `backend/server.js` — server, API, jadwal ingest, retensi, dan Socket.IO.
- `scripts/scraper/scrape_viral_daily.py` — feed berita serta pencarian provinsi.
- `scripts/scraper/free_social_scraper.py` — worker sosial otomatis per jam.
- `scripts/scraper/ytdlp_social_scraper.py` — pemeriksaan URL sosial yang diminta manual.
- `scripts/search_news.py` — pencarian sumber dan pembacaan isi artikel/postingan.
- `scripts/tools/verification_gate.py` — pemeriksaan bukti pada ingest.
- `scripts/nlp/` dan `models/sgd_model.pkl` — inferensi dan model lokal.
- `scripts/gazetteer/geocode.py` — ekstraksi lokasi.
- `frontend/` — dashboard WebGIS.

### Utilitas manual, lama, atau riset

Utilitas berikut bukan worker aktif yang dipanggil backend saat ini. Keberadaan file tidak membuatnya berjalan otomatis:

- `scripts/gazetteer/load_gazetteer.py` — pengisian awal gazetteer.
- `scripts/nlp/generate_dataset.py` dan `scripts/nlp/train.py` — penyusunan data dan pelatihan ulang model.
- `scripts/tools/reclassify_active.py` — pemeliharaan klasifikasi database.
- `scripts/scraper/rss_scraper.py` dan `scripts/scraper/twitter_import.py` — jalur scraper/impor lama.
- `scripts/simulator.py` — simulator lama yang mengharapkan endpoint `/api/simulator/*`; endpoint itu tidak disediakan server sekarang. Opsi reset simulator dapat mengubah status tampil langsung di database.
- `scripts/scraper/seed_national_threats.py` — seeder demo yang menjalankan `TRUNCATE social_threats`; ini menghapus data pada tabel tersebut bila dijalankan.
- `docs/research-hoax-platforms.md` — bahan riset historis, bukan konfigurasi sumber aplikasi terkini.

Pertimbangkan utilitas sebagai bahan peninjauan sebelum mengarsipkan atau menghapusnya. Dokumentasi ini tidak menghapus atau mengubah file tersebut.

## Menjalankan server

Siapkan koneksi PostgreSQL/PostGIS pada `.env`, tabel sesuai `data/schema.sql`, direktori gazetteer, model lokal, dependensi Node.js dan Python, serta `.venv/bin/python3`. Dari direktori proyek, jalankan:

```bash
./run.sh
```

Atau mulai backend langsung:

```bash
cd backend
npm start
```

Kemudian buka `http://localhost:3000`. Port dapat diatur melalui `PORT`; masa simpan dapat diatur melalui `DATA_RETENTION_DAYS`. Contoh variabel koneksi ada di `.env.example`.
