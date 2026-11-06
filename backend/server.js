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

    // Top 5 lokasi persebaran aktif
    const topRegionsQuery = `
      SELECT lokasi_nama, COUNT(*) as count
      FROM social_threats
      WHERE status_tayang = TRUE
      GROUP BY lokasi_nama
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

// 4. Analisis & Cek Berita Mandiri (Instant Fact-Checker)
app.post('/api/analyze', (req, res) => {
  const { text, save = false } = req.body;
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

        // Jika save=true, masukkan langsung ke database dan siarkan ke peta
        if (save && result.lokasi) {
          const insertQuery = `
            INSERT INTO social_threats 
            (sumber, teks, label, probabilitas, geom, lokasi_nama, url, status_tayang, dikoreksi)
            VALUES 
            ($1, $2, $3, $4, ST_SetSRID(ST_MakePoint($5, $6), 4326), $7, $8, TRUE, FALSE)
            RETURNING 
              id, sumber, teks, label, probabilitas, 
              ST_AsGeoJSON(geom) AS geojson, 
              lokasi_nama, url, status_tayang, dikoreksi, dibuat_pada;
          `;
          const dbRes = await pool.query(insertQuery, [
            'cek_mandiri',
            result.teks,
            result.label,
            result.probabilitas,
            result.lokasi.lon,
            result.lokasi.lat,
            result.lokasi.nama,
            'input_analis'
          ]);

          const feature = rowToGeoJSONFeature(dbRes.rows[0]);
          io.emit('new_threat', feature);
          result.saved_feature = feature;
        }

        res.json({ success: true, analysis: result });
      } catch (parseErr) {
        console.error('[!] Error parsing JSON hasil analisis:', parseErr);
        res.status(500).json({ error: 'Gagal memproses hasil analisis' });
      }
    }
  );
});

// ==========================================
// SIMULATOR BUFFER API
// ==========================================

let simulatorTimer = null;
let simulatorSpeed = 2500; // ms antar tayangan

async function executeSimulatorStep() {
  try {
    // Ambil 1 baris terlama atau acak yang belum tayang
    const query = `
      WITH next_threat AS (
        SELECT id FROM social_threats
        WHERE status_tayang = FALSE
        ORDER BY id ASC
        LIMIT 1
      )
      UPDATE social_threats
      SET status_tayang = TRUE, dibuat_pada = NOW()
      WHERE id IN (SELECT id FROM next_threat)
      RETURNING 
        id, sumber, teks, label, probabilitas, 
        ST_AsGeoJSON(geom) AS geojson, 
        lokasi_nama, url, status_tayang, dikoreksi, dibuat_pada;
    `;
    const result = await pool.query(query);

    if (result.rows.length === 0) {
      console.log('[i] Buffer habis! Semua data telah ditayangkan.');
      stopSimulator();
      io.emit('simulator_buffer_empty');
      return null;
    }

    const row = result.rows[0];
    const feature = rowToGeoJSONFeature(row);

    // Broadcast ke seluruh klien Leaflet via WebSocket!
    io.emit('new_threat', feature);
    console.log(`[+] [Simulator] ID ${feature.id} ditayangkan: [${feature.properties.label.toUpperCase()}] di ${feature.properties.lokasi_nama}`);
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
  console.log(`[✓] Simulator aktif (interval ${simulatorSpeed}ms)`);
}

function stopSimulator() {
  if (simulatorTimer) {
    clearInterval(simulatorTimer);
    simulatorTimer = null;
  }
  io.emit('simulator_status', { isRunning: false });
  console.log('[✓] Simulator dihentikan');
}

// Step manual 1 kali
app.post('/api/simulator/step', async (req, res) => {
  const feature = await executeSimulatorStep();
  if (feature) {
    res.json({ success: true, feature });
  } else {
    res.json({ success: false, message: 'Buffer habis atau tidak ada data yang belum tayang' });
  }
});

// Mulai loop otomatis
app.post('/api/simulator/start', (req, res) => {
  const speed = parseInt(req.body.speed, 10) || 2500;
  startSimulator(speed);
  res.json({ success: true, isRunning: true, speed });
});

// Hentikan loop otomatis
app.post('/api/simulator/stop', (req, res) => {
  stopSimulator();
  res.json({ success: true, isRunning: false });
});

// Reset semua status_tayang ke FALSE
app.post('/api/simulator/reset', async (req, res) => {
  stopSimulator();
  try {
    await pool.query('UPDATE social_threats SET status_tayang = FALSE, dikoreksi = FALSE;');
    io.emit('simulator_reset');
    console.log('[✓] Buffer berhasil di-reset: seluruh data disetel status_tayang = FALSE');
    res.json({ success: true, message: 'Buffer simulasi berhasil direset ke kondisi awal' });
  } catch (err) {
    console.error('[!] Error simulator reset:', err);
    res.status(500).json({ error: 'Gagal mereset buffer simulasi' });
  }
});

// Status simulator
app.get('/api/simulator/status', async (req, res) => {
  try {
    const unshownRes = await pool.query('SELECT COUNT(*) AS remaining FROM social_threats WHERE status_tayang = FALSE;');
    const shownRes = await pool.query('SELECT COUNT(*) AS active FROM social_threats WHERE status_tayang = TRUE;');
    res.json({
      isRunning: Boolean(simulatorTimer),
      speed: simulatorSpeed,
      remaining_buffer: parseInt(unshownRes.rows[0].remaining, 10),
      active_count: parseInt(shownRes.rows[0].active, 10)
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
