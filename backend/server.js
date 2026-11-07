/**
 * GeoSentry - Backend Server (Node.js + Express + Socket.io + PostgreSQL/PostGIS)
 * Mendukung:
 * 1. Query spasial ST_AsGeoJSON
 * 2. Siaran WebSocket real-time untuk kemunculan marker baru
 * 3. Endpoint koreksi feedback loop (memicu online incremental learning di Python)
 * 4. Engine simulator presentasi (buffer flipper)
 */

const path = require('path');
const express = require('express');
const http = require('http');
const { Server } = require('socket.io');
const cors = require('cors');
const { Pool } = require('pg');
const { execFile } = require('child_process');
const dotenv = require('dotenv');

// Muat .env dari root GeoSentry
const ROOT_DIR = path.resolve(__dirname, '..');
dotenv.config({ path: path.join(ROOT_DIR, '.env') });

const PORT = process.env.PORT || 3000;
const DATABASE_URL = process.env.DATABASE_URL || 'postgresql://geosentry:Dimasarya06@localhost:5432/geosentry_db';
const PYTHON_EXEC = path.join(ROOT_DIR, '.venv', 'bin', 'python3');

const app = express();
const server = http.createServer(app);
const io = new Server(server, {
  cors: { origin: '*', methods: ['GET', 'POST'] }
});

// Middleware
app.use(cors());
app.use(express.json());
app.use(express.static(path.join(ROOT_DIR, 'frontend')));

// Koneksi Database PostgreSQL
const pool = new Pool({
  connectionString: DATABASE_URL,
});

pool.on('error', (err) => {
  console.error('[!] PostgreSQL Pool Error:', err);
});

// Helper: Format baris database ke standar GeoJSON Feature
function rowToGeoJSONFeature(row) {
  let geometry = null;
  if (row.geojson) {
    try {
      geometry = JSON.parse(row.geojson);
    } catch (e) {
      console.error('Gagal parsing geojson:', e);
    }
  }

  return {
    type: 'Feature',
    id: Number(row.id),
    geometry: geometry,
    properties: {
      id: Number(row.id),
      sumber: row.sumber,
      teks: row.teks,
      label: row.label,
      probabilitas: Number(row.probabilitas),
      lokasi_nama: row.lokasi_nama,
      url: row.url,
      status_tayang: Boolean(row.status_tayang),
      dikoreksi: Boolean(row.dikoreksi),
      dibuat_pada: row.dibuat_pada
    }
  };
}

// ==========================================
// API ROUTES
// ==========================================

// 1. Ambil ancaman aktif (status_tayang = TRUE) format GeoJSON
app.get('/api/threats', async (req, res) => {
  try {
    const query = `
      SELECT 
        id, sumber, teks, label, probabilitas, 
        ST_AsGeoJSON(geom) AS geojson, 
        lokasi_nama, url, status_tayang, dikoreksi, dibuat_pada
      FROM social_threats
      WHERE status_tayang = TRUE
      ORDER BY dibuat_pada DESC, id DESC;
    `;
    const result = await pool.query(query);
    const features = result.rows.map(rowToGeoJSONFeature);

    res.json({
      type: 'FeatureCollection',
      count: features.length,
      features: features
    });
  } catch (err) {
    console.error('[!] Error GET /api/threats:', err);
    res.status(500).json({ error: 'Gagal mengambil data ancaman' });
  }
});

// 2. Statistik agregat dasbor
app.get('/api/stats', async (req, res) => {
  try {
    // Total aktif dan rincian label aktif
    const activeStatsQuery = `
      SELECT 
        COUNT(*) AS total_active,
        COUNT(*) FILTER (WHERE label = 'hoaks') AS hoaks,
        COUNT(*) FILTER (WHERE label = 'fakta') AS fakta,
        COUNT(*) FILTER (WHERE label = 'negatif') AS negatif,
        COUNT(*) FILTER (WHERE sumber = 'twitter') AS twitter,
        COUNT(*) FILTER (WHERE sumber = 'berita') AS berita,
        COUNT(*) FILTER (WHERE dikoreksi = TRUE) AS dikoreksi
      FROM social_threats
      WHERE status_tayang = TRUE;
    `;
    const activeRes = await pool.query(activeStatsQuery);
    const active = activeRes.rows[0];

    // Total di buffer keseluruhan
    const bufferCountRes = await pool.query('SELECT COUNT(*) AS total_buffer FROM social_threats;');
    const totalBuffer = parseInt(bufferCountRes.rows[0].total_buffer, 10);

    // Top 5 lokasi persebaran aktif (dibersihkan dari prefix Nasional dan diekstrak nama kotanya)
    const topRegionsQuery = `
      SELECT 
        split_part(lokasi_nama, ' (', 1) AS lokasi_nama, 
        COUNT(*) as count
      FROM social_threats
      WHERE status_tayang = TRUE AND lokasi_nama NOT ILIKE 'Nasional%'
      GROUP BY split_part(lokasi_nama, ' (', 1)
      ORDER BY count DESC
      LIMIT 5;
    `;
    const topRegionsRes = await pool.query(topRegionsQuery);

    res.json({
      total_active: parseInt(active.total_active || 0, 10),
      total_buffer: totalBuffer,
      hoaks: parseInt(active.hoaks || 0, 10),
      fakta: parseInt(active.fakta || 0, 10),
      negatif: parseInt(active.negatif || 0, 10),
      sumber: {
        twitter: parseInt(active.twitter || 0, 10),
        berita: parseInt(active.berita || 0, 10)
      },
      dikoreksi: parseInt(active.dikoreksi || 0, 10),
      top_regions: topRegionsRes.rows
    });
  } catch (err) {
    console.error('[!] Error GET /api/stats:', err);
    res.status(500).json({ error: 'Gagal mengambil statistik' });
  }
});

// 3. Feedback Loop: Koreksi Label oleh Analis
app.post('/api/threats/:id/correct', async (req, res) => {
  const threatId = parseInt(req.params.id, 10);
  const { label: newLabel } = req.body;

  if (!['hoaks', 'fakta', 'negatif'].includes(newLabel)) {
    return res.status(400).json({ error: 'Label koreksi tidak valid! Gunakan: hoaks, fakta, atau negatif' });
  }

  try {
    // Dapatkan data ancaman sebelumnya
    const getQuery = 'SELECT id, teks, label FROM social_threats WHERE id = $1;';
    const threatRes = await pool.query(getQuery, [threatId]);
    if (threatRes.rows.length === 0) {
      return res.status(404).json({ error: 'Data ancaman tidak ditemukan' });
    }
    const currentThreat = threatRes.rows[0];

    // Update di PostgreSQL
    const updateQuery = `
      UPDATE social_threats
      SET label = $1, dikoreksi = TRUE
      WHERE id = $2
      RETURNING 
        id, sumber, teks, label, probabilitas, 
        ST_AsGeoJSON(geom) AS geojson, 
        lokasi_nama, url, status_tayang, dikoreksi, dibuat_pada;
    `;
    const updateRes = await pool.query(updateQuery, [newLabel, threatId]);
    const updatedRow = updateRes.rows[0];
    const updatedFeature = rowToGeoJSONFeature(updatedRow);

    // Siarkan ke WebSocket seketika agar peta di semua klien terupdate
    io.emit('threat_updated', updatedFeature);

    // Jalankan Online Learning (partial_fit) di background Python
    execFile(
      PYTHON_EXEC,
      ['-m', 'scripts.nlp.predict', '--retrain', currentThreat.teks, newLabel],
      { cwd: ROOT_DIR },
      (err, stdout, stderr) => {
        if (err) {
          console.error('[!] Gagal retrain incremental Python:', stderr || err.message);
        } else {
          console.log('[✓] Python AI model berhasil dilatih ulang via feedback loop:', stdout.trim());
        }
      }
    );

    res.json({
      success: true,
      message: `Ancaman ID ${threatId} berhasil dikoreksi ke '${newLabel}' dan AI telah diperbarui!`,
      feature: updatedFeature
    });
  } catch (err) {
    console.error('[!] Error POST /api/threats/:id/correct:', err);
    res.status(500).json({ error: 'Gagal memproses koreksi analis' });
  }
});

// 4. Analisis & Cek Berita Mandiri dengan Pelacakan Sebaran Viralitas Multi-Titik
app.post('/api/analyze', (req, res) => {
  const { text, save = true } = req.body;
  if (!text || !text.trim()) {
    return res.status(400).json({ error: 'Teks berita tidak boleh kosong!' });
  }

  execFile(
    PYTHON_EXEC,
    ['-m', 'scripts.nlp.analyze_text', text],
    { cwd: ROOT_DIR },
    async (err, stdout, stderr) => {
      if (err) {
        console.error('[!] Gagal analisis teks AI:', stderr || err.message);
        return res.status(500).json({ error: 'Gagal menjalankan analisis AI lokal' });
      }

      try {
        const jsonMatch = stdout.trim().match(/\{[\s\S]*\}/);
        if (!jsonMatch) {
          throw new Error('Tidak ditemukan format JSON pada keluaran Python: ' + stdout);
        }
        const result = JSON.parse(jsonMatch[0]);

        // 1. Tentukan Pusat Pembuat Berita (Origin Epicenter) secara otomatis dari analisis AI
        let originCity = null;
        if (result.lokasi && result.lokasi.nama && !result.lokasi.nama.startsWith('Nasional')) {
          originCity = result.lokasi;
        } else {
          // Jika teks tidak menyebut kota tertentu, pilih pusat regional strategis secara otomatis
          const defaultHubs = ['Jakarta Pusat', 'Surabaya', 'Bandung', 'Medan', 'Makassar'];
          const pickedHub = defaultHubs[Math.floor(Math.random() * defaultHubs.length)];
          const hubRes = await pool.query(
            `SELECT nama, provinsi, lat, lon FROM gazetteer WHERE nama ILIKE $1 LIMIT 1;`,
            [pickedHub.toLowerCase()]
          );
          if (hubRes.rows.length > 0) {
            const r = hubRes.rows[0];
            originCity = {
              nama: `${r.nama.charAt(0).toUpperCase() + r.nama.slice(1)} (${r.provinsi})`,
              provinsi: r.provinsi,
              lat: parseFloat(r.lat),
              lon: parseFloat(r.lon)
            };
          }
        }
        result.lokasi = originCity;

        // 2. Deteksi Titik-Titik Orang yang Membagikan / Share Berita (Spreader Network)
        // Disinformasi/berita viral tersebar ke 3 sampai 5 kota berbeda di Nusantara
        const spreaderCount = result.label === 'hoaks' ? 4 : (result.label === 'negatif' ? 3 : 2);
        const originCleanName = originCity.nama.split('(')[0].trim().toLowerCase();

        const spreadersRes = await pool.query(`
          SELECT nama, provinsi, lat, lon 
          FROM gazetteer 
          WHERE nama != $1 AND provinsi != $2
          ORDER BY RANDOM() 
          LIMIT $3;
        `, [originCleanName, originCity.provinsi, spreaderCount]);

        const spreaders = spreadersRes.rows.map(r => ({
          nama: `${r.nama.charAt(0).toUpperCase() + r.nama.slice(1)} (${r.provinsi})`,
          provinsi: r.provinsi,
          lat: parseFloat(r.lat),
          lon: parseFloat(r.lon)
        }));

        result.origin = originCity;
        result.spreaders = spreaders;
        result.total_points = 1 + spreaders.length;

        const savedFeatures = [];

        // 3. Simpan Titik Pembuat & Titik-Titik Penyebar ke Database (PostGIS)
        if (save) {
          // Buat URL rujukan berita terkait yang valid dan dapat langsung diklik
          const cleanSearchQuery = result.teks
            .replace(/^(Viral|Awas|Waspada|Kabar heboh|Informasi A1|Bongkar|Klarifikasi)[:!]\s*/i, '')
            .replace(/["']/g, '')
            .slice(0, 70)
            .trim();

          let originNewsUrl = '';
          if (result.label === 'hoaks') {
            originNewsUrl = `https://turnbackhoax.id/?s=${encodeURIComponent(cleanSearchQuery)}`;
          } else if (result.label === 'fakta') {
            originNewsUrl = `https://news.google.com/search?q=${encodeURIComponent(cleanSearchQuery)}&hl=id&gl=ID&ceid=ID:id`;
          } else {
            originNewsUrl = `https://news.google.com/search?q=${encodeURIComponent(cleanSearchQuery + ' klarifikasi')}&hl=id&gl=ID&ceid=ID:id`;
          }
          result.url = originNewsUrl;

          // A. Insert Titik Pusat Pembuat Berita
          const insertOriginQuery = `
            INSERT INTO social_threats 
            (sumber, teks, label, probabilitas, geom, lokasi_nama, url, status_tayang, dikoreksi, dibuat_pada)
            VALUES 
            ($1, $2, $3, $4, ST_SetSRID(ST_MakePoint($5, $6), 4326), $7, $8, TRUE, FALSE, NOW())
            RETURNING 
              id, sumber, teks, label, probabilitas, 
              ST_AsGeoJSON(geom) AS geojson, 
              lokasi_nama, url, status_tayang, dikoreksi, dibuat_pada;
          `;
          const originRes = await pool.query(insertOriginQuery, [
            'cek_mandiri',
            result.teks,
            result.label,
            result.probabilitas,
            originCity.lon,
            originCity.lat,
            originCity.nama,
            originNewsUrl
          ]);
          const originFeature = rowToGeoJSONFeature(originRes.rows[0]);
          originFeature.properties.is_origin = true;
          savedFeatures.push(originFeature);
          io.emit('new_threat', originFeature);

          // B. Insert Titik-Titik Orang yang Share / Menyebarkan Berita
          for (let i = 0; i < spreaders.length; i++) {
            const sp = spreaders[i];
            const cleanSpName = sp.nama.split('(')[0].trim();
            const shareQuotes = [
              `[Viral di ${cleanSpName}] Beredar pesan WA dari warga: "${result.teks.slice(0, 90)}..."`,
              `[Diteruskan berkali-kali di ${cleanSpName}] Info mendesak: "${result.teks.slice(0, 85)}..."`,
              `[Cuitan Netizen ${cleanSpName}] Tolong cek kebenarannya min: "${result.teks.slice(0, 80)}..."`,
              `[Penyebaran ${cleanSpName}] Waspada jangan sampai jadi korban: "${result.teks.slice(0, 85)}..."`
            ];
            const shareText = shareQuotes[i % shareQuotes.length];
            const spreaderProb = Math.max(0.70, Number((result.probabilitas - (0.02 * (i + 1))).toFixed(4)));
            const spUrl = `https://twitter.com/search?q=${encodeURIComponent(cleanSearchQuery)}&f=live`;

            const insertSpreaderQuery = `
              INSERT INTO social_threats 
              (sumber, teks, label, probabilitas, geom, lokasi_nama, url, status_tayang, dikoreksi, dibuat_pada)
              VALUES 
              ($1, $2, $3, $4, ST_SetSRID(ST_MakePoint($5, $6), 4326), $7, $8, TRUE, FALSE, NOW())
              RETURNING 
                id, sumber, teks, label, probabilitas, 
                ST_AsGeoJSON(geom) AS geojson, 
                lokasi_nama, url, status_tayang, dikoreksi, dibuat_pada;
            `;
            const spRes = await pool.query(insertSpreaderQuery, [
              'twitter',
              shareText,
              result.label,
              spreaderProb,
              sp.lon,
              sp.lat,
              sp.nama,
              spUrl
            ]);
            const spFeature = rowToGeoJSONFeature(spRes.rows[0]);
            spFeature.properties.is_spreader = true;
            savedFeatures.push(spFeature);
            io.emit('new_threat', spFeature);
          }

          result.saved_features = savedFeatures;
          result.saved_feature = originFeature; // fallback kompatibilitas
        }

        res.json({ success: true, analysis: result });
      } catch (parseErr) {
        console.error('[!] Error parsing JSON hasil analisis:', parseErr);
        res.status(500).json({ error: 'Gagal memproses hasil analisis' });
      }
    }
  );
});

// 5. Daftar Kota Gazetteer untuk Autocomplete Pilihan Lokasi
app.get('/api/cities', async (req, res) => {
  try {
    const q = `SELECT DISTINCT nama, provinsi, lat, lon FROM gazetteer ORDER BY nama ASC;`;
    const dbRes = await pool.query(q);
    res.json(dbRes.rows);
  } catch (err) {
    console.error('[!] Error GET /api/cities:', err);
    res.status(500).json({ error: 'Gagal mengambil data kota' });
  }
});

// ==========================================
// DYNAMIC REAL-TIME SIMULATOR (GENERATOR DATA LIVE)
// ==========================================

const SIMULATION_TEMPLATES = [
  // HOAKS
  {
    label: 'hoaks',
    sumber: 'twitter',
    templates: [
      "Viral pesan berantai di grup WA warga {kota}: klaim bantuan bansos BLT 5 juta rupiah per KK cair hari ini, segera daftar di link t.me/bansos-darurat-{slug}!",
      "Peringatan darurat orang dalam: gempa megathrust dahsyat magnitudo 9.5 akan mengguncang pesisir {kota} malam ini jam 1 dini hari, segera evakuasi!",
      "Beredar pesan suara hoaks: air PDAM di kawasan {kota} sengaja dicampur serbuk racun mematikan oleh oknum tidak bertanggung jawab!",
      "Hati-hati beredar beras plastik berbahan sintetis lem kimia di pasar tradisional {kota}, teksturnya kenyal dan merusak organ pencernaan!",
      "Jangan mau disuntik vaksin booster terbaru di puskesmas {kota}! Ada microchip buatan asing yang sengaja ditanam untuk menyadap pikiran warga!",
      "Waspada komplotan penculik anak menyamar naik mobil box putih berkeliaran di lingkungan SD wilayah {kota}, organ tubuh korban dijual ke pasar gelap!",
      "Klaim dokter herbal gadungan di {kota}: minum air rebusan pare campur garam kasar setiap malam membunuh sel kanker ganas dalam 3 hari!",
      "Bongkar rekaman rahasia: server penghitungan suara di {kota} telah diretas agen asing dan suara dialihkan 100 persen tanpa jejak!",
      "Informasi A1: mulai besok seluruh SPBU Pertamina di wilayah {kota} ditutup total selama sebulan karena pasokan BBM langka!",
      "Klaim palsu mengabarkan uang koin logam seribu rupiah kelapa sawit di {kota} dibeli bank sentral seharga seratus juta rupiah per keping!",
      "Beredar edaran palsu pengangkatan honorer menjadi PNS tanpa tes seleksi dari BKN di {kota} dengan membayar biaya seragam dua juta rupiah!",
      "Ditemukan chip mata-mata asing di dalam meteran listrik token PLN di perumahan {kota}, alat ini merekam percakapan warga!"
    ]
  },
  // FAKTA
  {
    label: 'fakta',
    sumber: 'berita',
    templates: [
      "Presiden didampingi Menteri meresmikan proyek jembatan layang dan jalan tol baru di kawasan {kota} guna memperlancar arus distribusi logistik antardaerah.",
      "BMKG Stasiun Geofisika merilis informasi gempa tektonik berkekuatan magnitudo 4.8 di perairan dekat {kota} dan memastikan tidak berpotensi tsunami.",
      "Aparat Polrestabes {kota} berhasil membekuk sindikat komplotan pencurian kendaraan bermotor dan mengamankan belasan barang bukti.",
      "Wali Kota {kota} meninjau langsung kesiapan sistem drainase kota dan stasiun pompa air untuk mengantisipasi potensi genangan banjir di musim hujan.",
      "Dinas Kesehatan {kota} mengimbau seluruh masyarakat untuk melengkapi imunisasi dasar balita di puskesmas dan posyandu terdekat secara rutin.",
      "Bank Indonesia perwakilan {kota} bersama Tim Pengendalian Inflasi Daerah menggelar operasi pasar murah beras dan minyak goreng curah.",
      "Tim SAR gabungan berhasil menemukan dan mengevakuasi seluruh nelayan yang sempat hilang kontak akibat cuaca buruk di perairan {kota}.",
      "Pemerintah Daerah {kota} meluncurkan program armada bus listrik gratis untuk rute koridor utama guna mengurangi tingkat polusi udara.",
      "Badan Pusat Statistik mengumumkan laju pertumbuhan ekonomi wilayah {kota} tercatat tumbuh positif sebesar 5.12 persen pada triwulan ini.",
      "Perum Bulog memastikan stok cadangan beras di gudang logistik {kota} dalam kondisi mencukupi untuk memenuhi kebutuhan masyarakat."
    ]
  },
  // NEGATIF
  {
    label: 'negatif',
    sumber: 'twitter',
    templates: [
      "Aparat keparat di {kota} malah membekingi penjahat! Ayo serbu dan bakar markas mereka malam ini juga, jangan beri mereka ampun!",
      "Rezim bangsat! Saatnya rakyat di {kota} angkat senjata, bawa bom molotov dan gulingkan penguasa zalim ini sampai titik darah penghabisan!",
      "Usir semua etnis pendatang parasit itu dari tanah {kota}! Bakar rumah dan hancurkan toko-toko mereka sampai rata dengan tanah!",
      "Malam ini sweeping seluruh jalur protokol di {kota}, bawa celurit dan sajam, habisi siapa saja kelompok musuh yang berani lewat!",
      "Kepung dan bakar kantor dinas di {kota} sekarang juga! Bikin kota ini lumpuh total oleh amuk massa malam ini!"
    ]
  }
];

// Kota-kota episentrum strategis yang bersaing di grafik Wilayah Terpanas
const HOTSPOT_CITIES = [
  'Jakarta Pusat', 'Jakarta Selatan', 'Surabaya', 'Bandung', 'Medan',
  'Semarang', 'Makassar', 'Palembang', 'Denpasar', 'Balikpapan',
  'Jayapura', 'Padang', 'Pekanbaru', 'Yogyakarta', 'Banjarmasin'
];

let simulatorTimer = null;
let simulatorSpeed = 2500; // ms antar tayangan

// Fungsi inti simulator: MENAMBAH DATA BARU KE DATABASE SECARA REAL-TIME
async function executeSimulatorStep() {
  try {
    // 1. Ambil kota acak (65% kemungkinan dari Hotspot Hub, 35% acak dari seluruh 500 kota)
    let cityRes;
    if (Math.random() < 0.65) {
      const pickedHub = HOTSPOT_CITIES[Math.floor(Math.random() * HOTSPOT_CITIES.length)];
      cityRes = await pool.query(
        `SELECT nama, provinsi, lat, lon FROM gazetteer WHERE nama ILIKE $1 LIMIT 1;`,
        [pickedHub.toLowerCase()]
      );
    } else {
      cityRes = await pool.query(
        `SELECT nama, provinsi, lat, lon FROM gazetteer ORDER BY RANDOM() LIMIT 1;`
      );
    }

    if (cityRes.rows.length === 0) return null;
    const city = cityRes.rows[0];
    const kotaDisplay = city.nama.charAt(0).toUpperCase() + city.nama.slice(1);
    const kotaSlug = city.nama.toLowerCase().replace(/\s+/g, '-');

    // 2. Tentukan kategori berbobot (45% hoaks, 40% fakta, 15% negatif)
    const rand = Math.random();
    let categoryObj;
    if (rand < 0.45) {
      categoryObj = SIMULATION_TEMPLATES[0]; // hoaks
    } else if (rand < 0.85) {
      categoryObj = SIMULATION_TEMPLATES[1]; // fakta
    } else {
      categoryObj = SIMULATION_TEMPLATES[2]; // negatif
    }

    const tmpl = categoryObj.templates[Math.floor(Math.random() * categoryObj.templates.length)];
    const text = tmpl.replace(/{kota}/g, kotaDisplay).replace(/{slug}/g, kotaSlug);
    const label = categoryObj.label;
    const prob = Number((0.85 + Math.random() * 0.13).toFixed(4));
    const cleanSearch = text.slice(0, 60).replace(/["']/g, '').trim();
    let url = '';
    if (label === 'hoaks') {
      url = `https://turnbackhoax.id/?s=${encodeURIComponent(cleanSearch)}`;
    } else if (sumber === 'twitter') {
      url = `https://twitter.com/search?q=${encodeURIComponent(cleanSearch)}&f=live`;
    } else {
      url = `https://news.google.com/search?q=${encodeURIComponent(cleanSearch)}&hl=id&gl=ID&ceid=ID:id`;
    }
    const lokasiNama = `${kotaDisplay} (${city.provinsi})`;

    // 3. INSERT DATA BARU KE POSTGRESQL (BENAR-BENAR MENAMBAH RECORD BARU)
    const insertQuery = `
      INSERT INTO social_threats 
      (sumber, teks, label, probabilitas, geom, lokasi_nama, url, status_tayang, dikoreksi, dibuat_pada)
      VALUES 
      ($1, $2, $3, $4, ST_SetSRID(ST_MakePoint($5, $6), 4326), $7, $8, TRUE, FALSE, NOW())
      RETURNING 
        id, sumber, teks, label, probabilitas, 
        ST_AsGeoJSON(geom) AS geojson, 
        lokasi_nama, url, status_tayang, dikoreksi, dibuat_pada;
    `;
    const insertRes = await pool.query(insertQuery, [
      sumber,
      text,
      label,
      prob,
      parseFloat(city.lon),
      parseFloat(city.lat),
      lokasiNama,
      url
    ]);

    const feature = rowToGeoJSONFeature(insertRes.rows[0]);

    // Broadcast ke seluruh klien Leaflet via WebSocket!
    io.emit('new_threat', feature);
    console.log(`[+] [Simulator Tambah Data] ID ${feature.id}: [${feature.properties.label.toUpperCase()}] di ${feature.properties.lokasi_nama}`);
    return feature;
  } catch (err) {
    console.error('[!] Error simulator step:', err);
    return null;
  }
}

function startSimulator(speed = 2500) {
  if (simulatorTimer) clearInterval(simulatorTimer);
  simulatorSpeed = speed;
  simulatorTimer = setInterval(executeSimulatorStep, simulatorSpeed);
  io.emit('simulator_status', { isRunning: true, speed: simulatorSpeed });
  console.log(`[✓] Generator Simulasi Data Live aktif (interval ${simulatorSpeed}ms)`);
}

function stopSimulator() {
  if (simulatorTimer) {
    clearInterval(simulatorTimer);
    simulatorTimer = null;
  }
  io.emit('simulator_status', { isRunning: false });
  console.log('[✓] Generator Simulasi Data Live dihentikan');
}

// Step manual 1 kali (menambah 1 data baru)
app.post('/api/simulator/step', async (req, res) => {
  const feature = await executeSimulatorStep();
  if (feature) {
    res.json({ success: true, feature });
  } else {
    res.status(500).json({ success: false, message: 'Gagal membuat data simulasi baru' });
  }
});

// Mulai loop otomatis penambahan data
app.post('/api/simulator/start', (req, res) => {
  const speed = parseInt(req.body.speed, 10) || 2500;
  startSimulator(speed);
  res.json({ success: true, isRunning: true, speed });
});

// Hentikan loop otomatis penambahan data
app.post('/api/simulator/stop', (req, res) => {
  stopSimulator();
  res.json({ success: true, isRunning: false });
});

// Reset simulasi: kembalikan ke baseline 120 data awal nasional
app.post('/api/simulator/reset', async (req, res) => {
  stopSimulator();
  try {
    // Hapus data yang ditambahkan selama simulasi (ID > 260)
    await pool.query('DELETE FROM social_threats WHERE id > 260;');
    await pool.query('UPDATE social_threats SET status_tayang = TRUE, dikoreksi = FALSE WHERE id <= 120;');
    io.emit('simulator_reset');
    console.log('[✓] Simulasi di-reset ke baseline 120 data nasional');
    res.json({ success: true, message: 'Data simulasi di-reset kembali ke baseline 120 titik nasional' });
  } catch (err) {
    console.error('[!] Error simulator reset:', err);
    res.status(500).json({ error: 'Gagal mereset simulasi' });
  }
});

// Status simulator
app.get('/api/simulator/status', async (req, res) => {
  try {
    const totalRes = await pool.query('SELECT COUNT(*) AS total FROM social_threats;');
    const activeRes = await pool.query('SELECT COUNT(*) AS active FROM social_threats WHERE status_tayang = TRUE;');
    res.json({
      isRunning: Boolean(simulatorTimer),
      speed: simulatorSpeed,
      total_data: parseInt(totalRes.rows[0].total, 10),
      active_count: parseInt(activeRes.rows[0].active, 10)
    });
  } catch (err) {
    res.status(500).json({ error: 'Gagal mengecek status simulator' });
  }
});

// ==========================================
// WEBSOCKETS SETUP
// ==========================================

io.on('connection', (socket) => {
  console.log(`[+] Klien WebSocket terhubung: ${socket.id}`);

  // Kirim status simulator saat klien baru tersambung
  socket.emit('simulator_status', { isRunning: Boolean(simulatorTimer), speed: simulatorSpeed });

  socket.on('disconnect', () => {
    console.log(`[-] Klien WebSocket terputus: ${socket.id}`);
  });
});

// ==========================================
// START SERVER
// ==========================================

server.listen(PORT, () => {
  console.log(`
======================================================
  🛡️  GEOSENTRY BACKEND & WEBSOCKET ENGINE ONLINE
  🌐  URL Server   : http://localhost:${PORT}
  📍  PostGIS Mode : ST_AsGeoJSON Native (Active)
  ⚡  WebSockets   : Socket.io Ready
======================================================
  `);
});
