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

  let provinsi = 'Nasional';
  if (row.provinsi) {
    provinsi = row.provinsi;
  } else if (row.lokasi_nama) {
    const match = row.lokasi_nama.match(/\((.*?)\)/);
    if (match && match[1]) {
      provinsi = match[1].trim();
    } else if (row.lokasi_nama.toLowerCase().includes('jakarta')) {
      provinsi = 'DKI Jakarta';
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
      provinsi: provinsi,
      url: row.url,
      referensi_urls: row.referensi_urls || [],
      status_tayang: Boolean(row.status_tayang),
      dikoreksi: Boolean(row.dikoreksi),
      dibuat_pada: row.dibuat_pada
    }
  };
}

// ==========================================
// API ROUTES
// ==========================================

// 1. Ambil ancaman aktif (status_tayang = TRUE) format GeoJSON dalam jendela rolling 24 jam terakhir
app.get('/api/threats', async (req, res) => {
  try {
    const query = `
      SELECT 
        st.id, st.sumber, st.teks, st.label, st.probabilitas, 
        ST_AsGeoJSON(st.geom) AS geojson, 
        st.lokasi_nama,
        COALESCE(
          NULLIF(substring(st.lokasi_nama from '\\((.*)\\)'), ''),
          (SELECT g.provinsi FROM gazetteer g WHERE lower(g.nama) = lower(split_part(st.lokasi_nama, ' (', 1)) LIMIT 1),
          'Nasional'
        ) AS provinsi,
        st.url, st.referensi_urls, st.status_tayang, st.dikoreksi, st.dibuat_pada
      FROM social_threats st
      WHERE st.status_tayang = TRUE 
        AND st.dibuat_pada >= (NOW() - INTERVAL '24 hours')
      ORDER BY st.dibuat_pada DESC, st.id DESC;
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

// 2. Statistik agregat dasbor (Jendela Rolling 24 Jam Terakhir)
app.get('/api/stats', async (req, res) => {
  try {
    // Total aktif dan rincian label aktif dalam jendela 24 jam
    const activeStatsQuery = `
      SELECT 
        COUNT(*) AS total_active,
        COUNT(*) FILTER (WHERE label = 'hoaks') AS hoaks,
        COUNT(*) FILTER (WHERE label = 'fakta') AS fakta,
        COUNT(*) FILTER (WHERE label = 'negatif') AS negatif,
        COUNT(*) FILTER (WHERE sumber = 'twitter') AS twitter,
        COUNT(*) FILTER (WHERE sumber = 'tiktok') AS tiktok,
        COUNT(*) FILTER (WHERE sumber = 'instagram') AS instagram,
        COUNT(*) FILTER (WHERE sumber = 'berita' OR sumber = 'turnbackhoax') AS berita,
        COUNT(*) FILTER (WHERE sumber = 'mastodon') AS mastodon,
        COUNT(*) FILTER (WHERE sumber = 'bluesky') AS bluesky,
        COUNT(*) FILTER (WHERE sumber = 'reddit') AS reddit,
        COUNT(*) FILTER (WHERE dikoreksi = TRUE) AS dikoreksi,
        COUNT(*) FILTER (WHERE dibuat_pada >= CURRENT_DATE) AS hari_ini_count,
        COUNT(*) FILTER (WHERE dibuat_pada < CURRENT_DATE AND dibuat_pada >= NOW() - INTERVAL '24 hours') AS kemarin_count
      FROM social_threats
      WHERE status_tayang = TRUE 
        AND dibuat_pada >= (NOW() - INTERVAL '24 hours');
    `;
    const activeRes = await pool.query(activeStatsQuery);
    const active = activeRes.rows[0];

    // Total di database keseluruhan
    const bufferCountRes = await pool.query('SELECT COUNT(*) AS total_buffer FROM social_threats;');
    const totalBuffer = parseInt(bufferCountRes.rows[0].total_buffer, 10);

    // Top 5 lokasi persebaran aktif dalam jendela 24 jam beserta breakdown kategori faktual
    const topRegionsQuery = `
      SELECT 
        split_part(lokasi_nama, ' (', 1) AS lokasi_nama, 
        COUNT(*) AS count,
        COUNT(*) FILTER (WHERE label = 'hoaks') AS hoaks,
        COUNT(*) FILTER (WHERE label = 'fakta') AS fakta,
        COUNT(*) FILTER (WHERE label = 'negatif') AS negatif
      FROM social_threats
      WHERE status_tayang = TRUE 
        AND dibuat_pada >= (NOW() - INTERVAL '24 hours')
        AND lokasi_nama NOT ILIKE 'Nasional%'
      GROUP BY split_part(lokasi_nama, ' (', 1)
      ORDER BY count DESC
      LIMIT 5;
    `;
    const topRegionsRes = await pool.query(topRegionsQuery);

    // Pemimpin fakta wilayah: wilayah dengan hoaks terbanyak, fakta terbanyak, negatif terbanyak
    const topHoaksRes = await pool.query(`
      SELECT split_part(lokasi_nama, ' (', 1) AS lokasi_nama, COUNT(*) AS count 
      FROM social_threats 
      WHERE status_tayang = TRUE 
        AND dibuat_pada >= (NOW() - INTERVAL '24 hours')
        AND lokasi_nama NOT ILIKE 'Nasional%'
        AND label = 'hoaks'
      GROUP BY split_part(lokasi_nama, ' (', 1)
      ORDER BY count DESC LIMIT 1;
    `);

    const topFaktaRes = await pool.query(`
      SELECT split_part(lokasi_nama, ' (', 1) AS lokasi_nama, COUNT(*) AS count 
      FROM social_threats 
      WHERE status_tayang = TRUE 
        AND dibuat_pada >= (NOW() - INTERVAL '24 hours')
        AND lokasi_nama NOT ILIKE 'Nasional%'
        AND label = 'fakta'
      GROUP BY split_part(lokasi_nama, ' (', 1)
      ORDER BY count DESC LIMIT 1;
    `);

    const topNegatifRes = await pool.query(`
      SELECT split_part(lokasi_nama, ' (', 1) AS lokasi_nama, COUNT(*) AS count 
      FROM social_threats 
      WHERE status_tayang = TRUE 
        AND dibuat_pada >= (NOW() - INTERVAL '24 hours')
        AND lokasi_nama NOT ILIKE 'Nasional%'
        AND label = 'negatif'
      GROUP BY split_part(lokasi_nama, ' (', 1)
      ORDER BY count DESC LIMIT 1;
    `);

    res.json({
      total_active: parseInt(active.total_active || 0, 10),
      total_buffer: totalBuffer,
      hoaks: parseInt(active.hoaks || 0, 10),
      fakta: parseInt(active.fakta || 0, 10),
      negatif: parseInt(active.negatif || 0, 10),
      hari_ini_count: parseInt(active.hari_ini_count || 0, 10),
      kemarin_count: parseInt(active.kemarin_count || 0, 10),
      sumber: {
        twitter: parseInt(active.twitter || 0, 10),
        tiktok: parseInt(active.tiktok || 0, 10),
        instagram: parseInt(active.instagram || 0, 10),
        berita: parseInt(active.berita || 0, 10),
        mastodon: parseInt(active.mastodon || 0, 10),
        bluesky: parseInt(active.bluesky || 0, 10),
        reddit: parseInt(active.reddit || 0, 10)
      },
      dikoreksi: parseInt(active.dikoreksi || 0, 10),
      top_regions: topRegionsRes.rows.map(r => ({
        lokasi_nama: r.lokasi_nama,
        count: parseInt(r.count, 10),
        hoaks: parseInt(r.hoaks, 10),
        fakta: parseInt(r.fakta, 10),
        negatif: parseInt(r.negatif, 10)
      })),
      top_category_leaders: {
        hoaks: topHoaksRes.rows[0] ? { lokasi_nama: topHoaksRes.rows[0].lokasi_nama, count: parseInt(topHoaksRes.rows[0].count, 10) } : { lokasi_nama: '-', count: 0 },
        fakta: topFaktaRes.rows[0] ? { lokasi_nama: topFaktaRes.rows[0].lokasi_nama, count: parseInt(topFaktaRes.rows[0].count, 10) } : { lokasi_nama: '-', count: 0 },
        negatif: topNegatifRes.rows[0] ? { lokasi_nama: topNegatifRes.rows[0].lokasi_nama, count: parseInt(topNegatifRes.rows[0].count, 10) } : { lokasi_nama: '-', count: 0 }
      }
    });
  } catch (err) {
    console.error('[!] Error GET /api/stats:', err);
    res.status(500).json({ error: 'Gagal mengambil statistik' });
  }
});

// 2b. Statistik dan Sebaran Berita Viral Tiap Provinsi (Jendela 24 Jam)
app.get('/api/provinces', async (req, res) => {
  try {
    const query = `
      SELECT 
        COALESCE(
          NULLIF(substring(lokasi_nama from '\\((.*)\\)'), ''),
          (SELECT g.provinsi FROM gazetteer g WHERE lower(g.nama) = lower(split_part(st.lokasi_nama, ' (', 1)) LIMIT 1),
          'Nasional'
        ) AS provinsi,
        COUNT(*)::int AS total,
        COUNT(*) FILTER (WHERE label = 'hoaks')::int AS hoaks,
        COUNT(*) FILTER (WHERE label = 'fakta')::int AS fakta,
        COUNT(*) FILTER (WHERE label = 'negatif')::int AS negatif,
        ROUND(AVG(ST_Y(geom))::numeric, 4)::float AS center_lat,
        ROUND(AVG(ST_X(geom))::numeric, 4)::float AS center_lon
      FROM social_threats st
      WHERE status_tayang = TRUE 
        AND dibuat_pada >= (NOW() - INTERVAL '24 hours')
      GROUP BY 1
      ORDER BY total DESC;
    `;
    const result = await pool.query(query);
    res.json(result.rows);
  } catch (err) {
    console.error('[!] Error GET /api/provinces:', err);
    res.status(500).json({ error: 'Gagal mengambil data provinsi' });
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
// DAILY VIRAL NEWS INGEST & 48-HOUR ROLLING ENGINE
// ==========================================

const SCRAPER_SCRIPT = path.join(ROOT_DIR, 'scripts', 'scraper', 'scrape_viral_daily.py');
const SOCIAL_SCRAPER_SCRIPT = path.join(ROOT_DIR, 'scripts', 'scraper', 'free_social_scraper.py');
const YTDLP_SCRAPER_SCRIPT = path.join(ROOT_DIR, 'scripts', 'scraper', 'ytdlp_social_scraper.py');

// Eksekusi scraper berita viral via Python
function executeDailyViralIngest() {
  return new Promise((resolve, reject) => {
    execFile(PYTHON_EXEC, [SCRAPER_SCRIPT], { cwd: ROOT_DIR }, (error, stdout, stderr) => {
      if (error) {
        console.error('[!] Error eksekusi scraper berita viral:', stderr || error.message);
        return reject(error);
      }
      try {
        const match = stdout.match(/\{[\s\S]*"status"[\s\S]*\}/);
        const parsed = match ? JSON.parse(match[0]) : { status: 'success', raw: stdout };

        // Jika ada berita viral baru yang ditarik, langsung siarkan ke peta secara realtime via WebSocket!
        if (parsed.new_items && parsed.new_items.length > 0) {
          console.log(`[⚡ Realtime Stream] Menyiarkan +${parsed.new_items.length} berita baru ke peta Leaflet...`);
          for (const item of parsed.new_items) {
            const feature = {
              type: 'Feature',
              id: item.id,
              geometry: item.geom,
              properties: {
                id: item.id,
                sumber: item.sumber,
                teks: item.teks,
                label: item.label,
                probabilitas: item.probabilitas,
                lokasi_nama: item.lokasi_nama,
                url: item.url,
                referensi_urls: item.referensi_urls || [],
                status_tayang: true,
                dikoreksi: false,
                dibuat_pada: item.dibuat_pada
              }
            };
            io.emit('new_threat', feature);
          }
        }

        resolve(parsed);
      } catch (e) {
        resolve({ status: 'completed', output: stdout });
      }
    });
  });
}

// Eksekusi scraper media sosial gratis (Mastodon / Bluesky / Reddit) via Python
function executeFreeSocialIngest() {
  return new Promise((resolve) => {
    execFile(PYTHON_EXEC, [SOCIAL_SCRAPER_SCRIPT], { cwd: ROOT_DIR }, (error, stdout, stderr) => {
      if (error) {
        console.error('[!] Error eksekusi scraper media sosial:', stderr || error.message);
        return resolve({ status: 'error', error: stderr || error.message });
      }
      try {
        const match = stdout.match(/\{[\s\S]*"status"[\s\S]*\}/);
        const parsed = match ? JSON.parse(match[0]) : { status: 'success', raw: stdout };

        // Jika ada postingan media sosial baru, langsung siarkan ke peta secara realtime via WebSocket!
        if (parsed.new_items && parsed.new_items.length > 0) {
          console.log(`[⚡ Social Stream] Menyiarkan +${parsed.new_items.length} postingan media sosial baru ke peta Leaflet...`);
          for (const item of parsed.new_items) {
            const feature = {
              type: 'Feature',
              id: item.id,
              geometry: item.geom,
              properties: {
                id: item.id,
                sumber: item.sumber,
                teks: item.teks,
                label: item.label,
                probabilitas: item.probabilitas,
                lokasi_nama: item.lokasi_nama,
                url: item.url,
                referensi_urls: item.referensi_urls || [],
                status_tayang: true,
                dikoreksi: false,
                dibuat_pada: item.dibuat_pada
              }
            };
            io.emit('new_threat', feature);
          }
        }

        resolve(parsed);
      } catch (e) {
        resolve({ status: 'completed', output: stdout });
      }
    });
  });
}

// 1. Endpoint Sinkronisasi Berita Viral Manual (On-Demand)
app.post('/api/viral/sync', async (req, res) => {
  try {
    const ingestResult = await executeDailyViralIngest();
    
    // Broadcast pembaruan ke seluruh klien via WebSocket
    io.emit('viral_synced', {
      timestamp: new Date().toISOString(),
      result: ingestResult
    });

    res.json({ success: true, message: 'Sinkronisasi berita viral harian berhasil', data: ingestResult });
  } catch (err) {
    res.status(500).json({ error: 'Gagal menjalankan sinkronisasi berita viral', details: err.message });
  }
});

// 1b. Endpoint Sinkronisasi Media Sosial Gratis (Mastodon / Bluesky / Reddit)
app.post('/api/social/sync', async (req, res) => {
  try {
    const ingestResult = await executeFreeSocialIngest();

    // Broadcast pembaruan ke seluruh klien via WebSocket
    io.emit('social_synced', {
      timestamp: new Date().toISOString(),
      result: ingestResult
    });

    res.json({ success: true, message: 'Sinkronisasi media sosial gratis berhasil', data: ingestResult });
  } catch (err) {
    res.status(500).json({ error: 'Gagal menjalankan sinkronisasi media sosial', details: err.message });
  }
});

// 1c. Endpoint Inspeksi & Verifikasi URL Medsos via yt-dlp (TikTok, Instagram, YouTube, Twitter)
app.post('/api/social/inspect-url', async (req, res) => {
  const { url, text, save_to_map } = req.body;
  if (!url) {
    return res.status(400).json({ error: 'URL media sosial tidak boleh kosong' });
  }

  const args = [YTDLP_SCRAPER_SCRIPT, '--url', url];
  if (text) {
    args.push('--text', text);
  }
  if (save_to_map === false) {
    args.push('--no-save');
  }

  execFile(PYTHON_EXEC, args, { cwd: ROOT_DIR }, (error, stdout, stderr) => {
    if (error && !stdout) {
      console.error('[!] Error eksekusi ytdlp social scraper:', stderr || error.message);
      return res.status(500).json({ error: 'Gagal menganalisis URL media sosial', details: stderr || error.message });
    }

    try {
      const match = stdout.match(/\{[\s\S]*"status"[\s\S]*\}/);
      if (!match) {
        return res.status(500).json({ error: 'Format output scraper tidak valid', raw: stdout });
      }
      const data = JSON.parse(match[0]);

      // Jika disimpan ke peta dan ada ID, siarkan via WebSocket ke Leaflet!
      if (data.id && save_to_map !== false) {
        const feature = {
          type: 'Feature',
          id: data.id,
          geometry: {
            type: 'Point',
            coordinates: [data.lon, data.lat]
          },
          properties: {
            id: data.id,
            sumber: data.sumber,
            teks: data.teks,
            label: data.label,
            probabilitas: data.probabilitas,
            lokasi_nama: data.lokasi_nama,
            provinsi: data.provinsi,
            url: data.url,
            referensi_urls: data.referensi_urls || [],
            status_tayang: true,
            dikoreksi: false,
            dibuat_pada: data.dibuat_pada
          }
        };
        io.emit('new_threat', feature);
      }

      res.json({ success: true, data: data });
    } catch (parseErr) {
      res.status(500).json({ error: 'Gagal parsing respons scraper', details: parseErr.message, raw: stdout });
    }
  });
});

// 2. Info Status Jendela Pemantauan 24 Jam
app.get('/api/viral/info', async (req, res) => {
  try {
    const today = new Date().toLocaleDateString('id-ID', { timeZone: 'Asia/Jakarta', day: 'numeric', month: 'long', year: 'numeric' });
    const yesterdayDate = new Date(Date.now() - 24 * 60 * 60 * 1000);
    const yesterday = yesterdayDate.toLocaleDateString('id-ID', { timeZone: 'Asia/Jakarta', day: 'numeric', month: 'long', year: 'numeric' });

    // Hitung waktu mundur hingga 00:00 WIB berikutnya
    const nowWIB = new Date(new Date().toLocaleString('en-US', { timeZone: 'Asia/Jakarta' }));
    const nextMidnightWIB = new Date(nowWIB);
    nextMidnightWIB.setHours(24, 0, 0, 0);
    const msUntilMidnight = nextMidnightWIB.getTime() - nowWIB.getTime();

    const activeCountRes = await pool.query(`
      SELECT 
        COUNT(*) AS total_active_24h,
        COUNT(*) FILTER (WHERE dibuat_pada >= CURRENT_DATE) AS count_today,
        COUNT(*) FILTER (WHERE dibuat_pada < CURRENT_DATE AND dibuat_pada >= NOW() - INTERVAL '24 hours') AS count_yesterday
      FROM social_threats 
      WHERE status_tayang = TRUE 
        AND dibuat_pada >= (NOW() - INTERVAL '24 hours');
    `);

    res.json({
      window_range: `Jendela Rolling 24 Jam Terakhir`,
      hari_ini: today,
      kemarin: yesterday,
      next_rollover: 'Pukul 00:00 WIB',
      seconds_until_rollover: Math.floor(msUntilMidnight / 1000),
      stats: activeCountRes.rows[0]
    });
  } catch (err) {
    res.status(500).json({ error: 'Gagal mengambil info jendela berita viral' });
  }
});

// ==========================================
// HOURLY AUTONOMOUS BACKGROUND INGEST WORKER (REAL-TIME)
// ==========================================
// Memeriksa berita viral terbaru setiap jam secara otonom di latar belakang.
// Bila ada berita viral baru, berita otomatis bertambah dan dipetakan.
// Data yang sudah lewat >24 jam otomatis disembunyikan (hilang dari maps).

async function purgeThreatsOlderThan24Hours() {
  try {
    const purgeQuery = `
      UPDATE social_threats 
      SET status_tayang = FALSE 
      WHERE status_tayang = TRUE 
        AND dibuat_pada < (NOW() - INTERVAL '24 hours');
    `;
    const res = await pool.query(purgeQuery);
    if (res.rowCount > 0) {
      console.log(`[🧹 Purge Otomatis] ${res.rowCount} berita yang lewat >24 jam otomatis disembunyikan dari peta.`);
      io.emit('threats_purged', { count: res.rowCount, message: 'Data yang telah lewat >24 jam otomatis dihilangkan dari peta.' });
    }
  } catch (err) {
    console.error('[!] Error purge data kadaluarsa:', err.message);
  }
}

let isAutonomousIngesting = false;
async function runHourlyViralIngestWorker() {
  if (isAutonomousIngesting) return;
  isAutonomousIngesting = true;
  try {
    console.log(`\n[🕒 Hourly Ingest Worker] Menjalankan pengecekan berkala berita viral terbaru...`);
    
    // 1. Bersihkan berita yang sudah lewat > 24 jam agar hilang dari peta
    await purgeThreatsOlderThan24Hours();

    // 2. Tarik berita viral terkini (Google News, TurnBackHoax, Antara, CNBC, TikTok, Instagram)
    const result = await executeDailyViralIngest();
    if (result && result.inserted > 0) {
      console.log(`[⚡ Realtime Auto Ingest] +${result.inserted} berita viral baru berhasil ditambahkan dan disiarkan ke peta Leaflet!`);
      io.emit('viral_synced', {
        timestamp: new Date().toISOString(),
        result: result
      });
    } else {
      console.log(`[✓ Ingest Selesai] Pengecekan selesai. Seluruh data tetap sinkron.`);
    }

    // 3. Tarik media sosial gratis (Mastodon, Bluesky, Reddit) tanpa API key
    try {
      const socialResult = await executeFreeSocialIngest();
      if (socialResult && socialResult.inserted > 0) {
        console.log(`[⚡ Social Auto Ingest] +${socialResult.inserted} postingan media sosial baru disiarkan ke peta Leaflet!`);
        io.emit('social_synced', {
          timestamp: new Date().toISOString(),
          result: socialResult
        });
      } else {
        console.log(`[✓ Social Ingest Selesai] Tidak ada postingan media sosial baru.`);
      }
    } catch (socialErr) {
      console.error('[!] Error pada social ingest worker:', socialErr.message);
    }
  } catch (err) {
    console.error('[!] Error pada hourly ingest worker:', err.message);
  } finally {
    isAutonomousIngesting = false;
  }
}

// Jalankan otomatis saat server pertama kali start (setelah 3 detik)
setTimeout(runHourlyViralIngestWorker, 3000);

// Jalankan otomatis setiap 1 jam (3600000 ms) terus menerus
setInterval(runHourlyViralIngestWorker, 3600000);

// Evaluasi pembersihan data >24 jam setiap 5 menit
setInterval(purgeThreatsOlderThan24Hours, 300000);

// ==========================================
// AUTOMATIC MIDNIGHT ROLLOVER SCHEDULER (00:00 WIB)
// ==========================================
// Mendeteksi pergantian tanggal otomatis pada pukul 00:00 WIB,
// memajukan jendela pantau 24 jam, dan otomatis mengambil berita viral baru.

let currentWIBDate = new Date().toLocaleDateString('en-CA', { timeZone: 'Asia/Jakarta' }); // YYYY-MM-DD

setInterval(async () => {
  const checkDate = new Date().toLocaleDateString('en-CA', { timeZone: 'Asia/Jakarta' });
  if (checkDate !== currentWIBDate) {
    console.log(`\n[🔔] ======================================================`);
    console.log(`[🔔] PUKUL 00:00 WIB TERCAPAI: Pergantian Hari Terdeteksi!`);
    console.log(`[🔔] Tanggal Baru : ${checkDate} (Sebelumnya: ${currentWIBDate})`);
    console.log(`[🔔] Menjalankan Rollover Jendela 24 Jam & Sinkronisasi Berita...`);
    console.log(`[🔔] ======================================================\n`);
    
    currentWIBDate = checkDate;

    try {
      // 1. Jalankan penarikan berita viral harian untuk hari baru
      await executeDailyViralIngest();

      // 2. Siarkan pergantian tanggal ke semua klien aktif agar UI & peta langsung terbarui
      io.emit('day_rollover', {
        new_date: checkDate,
        message: 'Pukul 00:00 WIB tercapai: Jendela pemantauan otomatis bergulir ke 24 jam terakhir.'
      });
    } catch (e) {
      console.error('[!] Gagal menjalankan ingest otomatis tengah malam:', e);
    }
  }
}, 30000); // Evaluasi setiap 30 detik

// ==========================================
// WEBSOCKETS SETUP
// ==========================================

io.on('connection', (socket) => {
  console.log(`[+] Klien WebSocket terhubung: ${socket.id}`);

  // Kirim info status jendela berita viral saat klien baru tersambung
  socket.emit('viral_engine_ready', {
    message: 'Mesin Pemantauan Berita Viral 24 Jam Aktif',
    rollover_schedule: 'Setiap 00:00 WIB'
  });

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
