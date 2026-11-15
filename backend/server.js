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
const { execFile, spawn } = require('child_process');
const dotenv = require('dotenv');

// Muat .env dari root GeoSentry
const ROOT_DIR = path.resolve(__dirname, '..');
dotenv.config({ path: path.join(ROOT_DIR, '.env') });

const PORT = process.env.PORT || 3000;
const DATABASE_URL = process.env.DATABASE_URL || 'postgresql://geosentry:Dimasarya06@localhost:5432/geosentry_db';
const PYTHON_EXEC = path.join(ROOT_DIR, '.venv', 'bin', 'python3');
const configuredRetentionDays = Number.parseInt(process.env.DATA_RETENTION_DAYS || '90', 10);
const DATA_RETENTION_DAYS = Number.isInteger(configuredRetentionDays) && configuredRetentionDays > 0
  ? configuredRetentionDays
  : 90;

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

async function ensureVerificationSchema() {
  await pool.query(`
    ALTER TABLE social_threats
      ADD COLUMN IF NOT EXISTS verifikasi_status VARCHAR(20) NOT NULL DEFAULT 'pending',
      ADD COLUMN IF NOT EXISTS verifikasi_dasar TEXT,
      ADD COLUMN IF NOT EXISTS verifikasi_pada TIMESTAMPTZ;

    UPDATE social_threats
    SET status_tayang = FALSE
    WHERE COALESCE(verifikasi_status, 'pending') NOT IN ('terverifikasi', 'prediksi_sementara');

    CREATE OR REPLACE FUNCTION geosentry_require_verification_before_display()
    RETURNS TRIGGER AS $$
    BEGIN
      IF NEW.status_tayang = TRUE AND COALESCE(NEW.verifikasi_status, 'pending') NOT IN ('terverifikasi', 'prediksi_sementara') THEN
        NEW.status_tayang := FALSE;
      END IF;
      RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;

    DROP TRIGGER IF EXISTS geosentry_verification_before_display ON social_threats;
    CREATE TRIGGER geosentry_verification_before_display
      BEFORE INSERT OR UPDATE OF status_tayang, verifikasi_status ON social_threats
      FOR EACH ROW EXECUTE FUNCTION geosentry_require_verification_before_display();
  `);
}

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
    if (row.lokasi_nama.toLowerCase().startsWith('nasional')) {
      provinsi = 'Nasional';
    } else {
      const match = row.lokasi_nama.match(/\((.*?)\)/);
      if (match && match[1]) {
        provinsi = match[1].trim();
      } else if (row.lokasi_nama.toLowerCase().includes('jakarta')) {
        provinsi = 'DKI Jakarta';
      }
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
      verifikasi_status: row.verifikasi_status || 'pending',
      verifikasi_dasar: row.verifikasi_dasar || null,
      verifikasi_pada: row.verifikasi_pada || null,
      dikoreksi: Boolean(row.dikoreksi),
      dibuat_pada: row.dibuat_pada
    }
  };
}

const SEARCH_SYNONYM_GROUPS = [
  ['berhenti', 'mundur', 'mengundurkan diri', 'resign', 'lengser'],
  ['wapres', 'wakil presiden'],
  ['naik', 'meningkat', 'kenaikan', 'peningkatan', 'bertambah', 'menanjak'],
  ['turun', 'menurun', 'penurunan', 'berkurang'],
  ['meninggal', 'wafat', 'tewas'],
  ['ditangkap', 'diamankan', 'diciduk', 'ditahan'],
  ['membantah', 'menepis', 'menyangkal'],
  ['demo', 'demonstrasi', 'unjuk rasa'],
  ['hoaks', 'hoax', 'kabar bohong', 'berita palsu'],
  ['gempa', 'gempa bumi'],
  ['bantuan', 'bansos', 'bantuan sosial'],
  ['pemilu', 'pemilihan umum']
];

function publisherGroup(domain = '') {
  const labels = String(domain).toLowerCase().replace(/^www\./, '').split('.');
  if (labels.length < 2) return domain;
  if (labels.length >= 3 && ['co', 'com', 'gov', 'org', 'ac', 'net', 'or'].includes(labels.at(-2))) {
    return labels.slice(-3).join('.');
  }
  return labels.slice(-2).join('.');
}

function isOfficialPrimaryDomain(domain = '') {
  const host = String(domain).toLowerCase().replace(/^www\./, '');
  return host.endsWith('.go.id') || host.endsWith('.ac.id') || host.endsWith('.gov') || host.endsWith('.gov.id');
}

async function loadRecentSocialPosts(query = '', limit = 40) {
  const ignored = new Set(['yang', 'dan', 'atau', 'jadi', 'dari', 'sebagai', 'ke', 'di', 'untuk', 'pada', 'dengan', 'berita', 'kabar']);
  const terms = new Set(String(query).toLocaleLowerCase('id-ID').split(/[^a-z0-9]+/).filter(term => term.length > 2 && !ignored.has(term)));
  for (const group of SEARCH_SYNONYM_GROUPS) {
    if (group.some(term => terms.has(term))) group.forEach(term => terms.add(term));
  }
  const patterns = [...terms].slice(0, 24).map(term => `%${term}%`);
  const result = await pool.query(`
    SELECT sumber, teks, url, dibuat_pada
    FROM social_threats
    WHERE status_tayang = TRUE
      AND verifikasi_status = 'terverifikasi'
      AND sumber IN ('twitter', 'x', 'bluesky', 'mastodon', 'reddit', 'tiktok', 'instagram')
      AND url IS NOT NULL
      AND url !~* '(search\\?|explore/tags)'
      AND url !~* '(news\\.google\\.com/rss|bing\\.com/news)'
      AND dibuat_pada >= NOW() - INTERVAL '90 days'
      AND teks ILIKE ANY($1::text[])
    ORDER BY dibuat_pada DESC
    LIMIT $2;
  `, [patterns, limit]);
  return result.rows.map(row => ({
    sumber: row.sumber,
    teks: String(row.teks || '').slice(0, 700),
    url: row.url,
    dibuat_pada: row.dibuat_pada
  }));
}

function runNewsSearchWorker(query, socialPosts = [], timeoutMs = 45000) {
  return new Promise((resolve, reject) => {
    const worker = spawn(
      PYTHON_EXEC,
      [path.join(ROOT_DIR, 'scripts', 'search_news.py'), query],
      { cwd: ROOT_DIR, stdio: ['pipe', 'pipe', 'pipe'] }
    );
    let stdout = '';
    let stderr = '';
    let timedOut = false;
    let outputTooLarge = false;
    const timeout = setTimeout(() => {
      timedOut = true;
      worker.kill('SIGKILL');
    }, timeoutMs);
    worker.stdout.on('data', chunk => {
      stdout += chunk.toString();
      if (stdout.length > 4 * 1024 * 1024) {
        outputTooLarge = true;
        worker.kill('SIGKILL');
      }
    });
    worker.stderr.on('data', chunk => {
      if (stderr.length < 64000) stderr += chunk.toString();
    });
    worker.on('error', err => {
      clearTimeout(timeout);
      reject(err);
    });
    worker.on('close', code => {
      clearTimeout(timeout);
      if (timedOut) return reject(new Error('Pencarian sumber melewati batas waktu.'));
      if (outputTooLarge) return reject(new Error('Respons pencarian terlalu besar.'));
      if (code !== 0) return reject(new Error(stderr || `Worker pencarian berhenti (${code}).`));
      try {
        resolve(JSON.parse(stdout.trim()));
      } catch (err) {
        reject(new Error(`Respons pencarian tidak valid: ${err.message}`));
      }
    });
    worker.stdin.on('error', err => {
      if (err.code !== 'EPIPE') stderr += err.message;
    });
    worker.stdin.end(JSON.stringify(socialPosts));
  });
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
        CASE
          WHEN st.lokasi_nama ILIKE 'Nasional%' THEN 'Nasional'
          ELSE COALESCE(
            NULLIF(substring(st.lokasi_nama from '\\((.*)\\)'), ''),
            (SELECT g.provinsi FROM gazetteer g WHERE lower(g.nama) = lower(split_part(st.lokasi_nama, ' (', 1)) LIMIT 1),
            'Nasional'
          )
        END AS provinsi,
        st.url, st.referensi_urls, st.status_tayang, st.verifikasi_status,
        st.verifikasi_dasar, st.verifikasi_pada, st.dikoreksi, st.dibuat_pada
      FROM social_threats st
      WHERE st.status_tayang = TRUE 
        AND st.verifikasi_status IN ('terverifikasi', 'prediksi_sementara')
        AND st.label IN ('hoaks', 'fakta')
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
        COUNT(*) FILTER (WHERE verifikasi_status = 'prediksi_sementara') AS prediksi_sementara,
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
        AND verifikasi_status IN ('terverifikasi', 'prediksi_sementara')
        AND label IN ('hoaks', 'fakta')
        AND dibuat_pada >= (NOW() - INTERVAL '24 hours');
    `;
    const activeRes = await pool.query(activeStatsQuery);
    const active = activeRes.rows[0];

    // Total di database keseluruhan
    const bufferCountRes = await pool.query("SELECT COUNT(*) AS total_buffer FROM social_threats WHERE label IN ('hoaks', 'fakta') AND verifikasi_status IN ('terverifikasi', 'prediksi_sementara');");
    const totalBuffer = parseInt(bufferCountRes.rows[0].total_buffer, 10);

    // Top 5 lokasi persebaran aktif dalam jendela 24 jam beserta breakdown kategori faktual
    const topRegionsQuery = `
      SELECT 
        CASE 
          WHEN split_part(lokasi_nama, ' (', 1) ILIKE 'Jakarta%' THEN 'Jakarta'
          ELSE split_part(lokasi_nama, ' (', 1)
        END AS lokasi_nama, 
        COUNT(*) AS count,
        COUNT(*) FILTER (WHERE label = 'hoaks') AS hoaks,
        COUNT(*) FILTER (WHERE label = 'fakta') AS fakta
      FROM social_threats
      WHERE status_tayang = TRUE 
        AND verifikasi_status IN ('terverifikasi', 'prediksi_sementara')
        AND label IN ('hoaks', 'fakta')
        AND dibuat_pada >= (NOW() - INTERVAL '24 hours')
        AND lokasi_nama NOT ILIKE 'Nasional%'
      GROUP BY 1
      ORDER BY count DESC
      LIMIT 5;
    `;
    const topRegionsRes = await pool.query(topRegionsQuery);

    // Wilayah dengan jumlah hoaks dan fakta terbanyak.
    const topHoaksRes = await pool.query(`
      SELECT 
        CASE 
          WHEN split_part(lokasi_nama, ' (', 1) ILIKE 'Jakarta%' THEN 'Jakarta'
          ELSE split_part(lokasi_nama, ' (', 1)
        END AS lokasi_nama, 
        COUNT(*) AS count 
      FROM social_threats 
      WHERE status_tayang = TRUE 
        AND verifikasi_status IN ('terverifikasi', 'prediksi_sementara')
        AND dibuat_pada >= (NOW() - INTERVAL '24 hours')
        AND lokasi_nama NOT ILIKE 'Nasional%'
        AND label = 'hoaks'
      GROUP BY 1
      ORDER BY count DESC LIMIT 1;
    `);

    const topFaktaRes = await pool.query(`
      SELECT 
        CASE 
          WHEN split_part(lokasi_nama, ' (', 1) ILIKE 'Jakarta%' THEN 'Jakarta'
          ELSE split_part(lokasi_nama, ' (', 1)
        END AS lokasi_nama, 
        COUNT(*) AS count 
      FROM social_threats 
      WHERE status_tayang = TRUE 
        AND verifikasi_status IN ('terverifikasi', 'prediksi_sementara')
        AND dibuat_pada >= (NOW() - INTERVAL '24 hours')
        AND lokasi_nama NOT ILIKE 'Nasional%'
        AND label = 'fakta'
      GROUP BY 1
      ORDER BY count DESC LIMIT 1;
    `);

    res.json({
      total_active: parseInt(active.total_active || 0, 10),
      total_buffer: totalBuffer,
      prediksi_sementara: parseInt(active.prediksi_sementara || 0, 10),
      hoaks: parseInt(active.hoaks || 0, 10),
      fakta: parseInt(active.fakta || 0, 10),
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
        fakta: parseInt(r.fakta, 10)
      })),
      top_category_leaders: {
        hoaks: topHoaksRes.rows[0] ? { lokasi_nama: topHoaksRes.rows[0].lokasi_nama, count: parseInt(topHoaksRes.rows[0].count, 10) } : { lokasi_nama: '-', count: 0 },
        fakta: topFaktaRes.rows[0] ? { lokasi_nama: topFaktaRes.rows[0].lokasi_nama, count: parseInt(topFaktaRes.rows[0].count, 10) } : { lokasi_nama: '-', count: 0 }
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
        CASE
          WHEN st.lokasi_nama ILIKE 'Nasional%' THEN 'Nasional'
          ELSE COALESCE(
            NULLIF(substring(lokasi_nama from '\\((.*)\\)'), ''),
            (SELECT g.provinsi FROM gazetteer g WHERE lower(g.nama) = lower(split_part(st.lokasi_nama, ' (', 1)) LIMIT 1),
            'Nasional'
          )
        END AS provinsi,
        COUNT(*)::int AS total,
        COUNT(*) FILTER (WHERE label = 'hoaks')::int AS hoaks,
        COUNT(*) FILTER (WHERE label = 'fakta')::int AS fakta,
        ROUND(AVG(ST_Y(geom))::numeric, 4)::float AS center_lat,
        ROUND(AVG(ST_X(geom))::numeric, 4)::float AS center_lon
      FROM social_threats st
      WHERE status_tayang = TRUE 
        AND verifikasi_status IN ('terverifikasi', 'prediksi_sementara')
        AND label IN ('hoaks', 'fakta')
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

  if (!['hoaks', 'fakta'].includes(newLabel)) {
    return res.status(400).json({ error: 'Label koreksi tidak valid! Gunakan: hoaks atau fakta' });
  }

  try {
    // Dapatkan data ancaman sebelumnya
    const getQuery = "SELECT id, teks, label FROM social_threats WHERE id = $1 AND verifikasi_status IN ('terverifikasi', 'prediksi_sementara');";
    const threatRes = await pool.query(getQuery, [threatId]);
    if (threatRes.rows.length === 0) {
      return res.status(404).json({ error: 'Data ancaman tidak ditemukan' });
    }
    const currentThreat = threatRes.rows[0];

    // Update di PostgreSQL
    const updateQuery = `
      UPDATE social_threats
      SET label = $1, dikoreksi = TRUE, verifikasi_status = 'terverifikasi',
          verifikasi_dasar = 'dikoreksi analis melalui dashboard', verifikasi_pada = NOW()
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
      message: `Ancaman ID ${threatId} dikoreksi analis ke '${newLabel}' dan masukan disimpan untuk pembelajaran model.`,
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

        // Lokasi hanya boleh berasal dari lokasi yang benar-benar terdeteksi di teks.
        let originCity = null;
        if (result.lokasi && result.lokasi.nama && !result.lokasi.nama.startsWith('Nasional')) {
          originCity = result.lokasi;
        }
        result.lokasi = originCity;
        result.origin = originCity;
        result.spreaders = [];
        result.total_points = originCity ? 1 : 0;

        const savedFeatures = [];

        // Teks saja tidak memberi tahu URL sumber asli. Tautkan ke pencarian yang
        // memakai teks yang lebih utuh dan nyatakan dengan jelas bahwa itu pencarian.
        if (result.teks) {
          const cleanSearchQuery = result.teks
            .replace(/^(Viral|Awas|Waspada|Kabar heboh|Informasi A1|Bongkar|Klarifikasi)[:!]\s*/i, '')
            .replace(/["']/g, ' ')
            .replace(/\s+/g, ' ')
            .slice(0, 180)
            .trim();
          const submittedSourceUrl = /^https?:\/\/[^\s<>"']+$/i.test(result.teks.trim())
            ? result.teks.trim().replace(/[),.;!?]+$/, '')
            : null;
          result.related_sources = [];
          result.analysis_basis = 'prediksi model teks; belum diperiksa silang dengan sumber';
          try {
            const socialPosts = await loadRecentSocialPosts(cleanSearchQuery, 40);
            const sourceSearch = await runNewsSearchWorker(cleanSearchQuery, socialPosts);
            const relatedArticles = (sourceSearch.articles || []).filter(item => item.relevance >= 0.35);
            result.related_sources = relatedArticles.slice(0, 6).map(item => ({
              title: item.title,
              source: item.source,
              domain: item.domain,
              url: item.url,
              label: item.analysis?.label,
              confidence: item.analysis?.probabilitas,
              basis: item.analysis?.basis,
              verification_status: item.analysis?.status || 'prediksi_sementara',
              relevance: item.relevance,
              title_relevance: item.title_relevance,
              body_relevance: item.body_relevance,
              content_type: item.content_type || 'berita'
            }));

            if (['hoaks', 'fakta', 'negatif'].includes(sourceSearch.summary_label)) {
              result.label = sourceSearch.summary_label;
              result.probabilitas = Number(sourceSearch.summary_confidence) || Math.min(Number(result.probabilitas) || 0, 0.45);
              result.analysis_basis = sourceSearch.summary_basis || 'hasil pencocokan isi dan sumber terkait';
              result.verifikasi_status = sourceSearch.summary_status === 'terverifikasi'
                ? 'terverifikasi'
                : 'prediksi_sementara';
              result.verifikasi_dasar = result.verifikasi_status === 'terverifikasi'
                ? result.analysis_basis
                : null;
            } else {
              result.verifikasi_status = 'prediksi_sementara';
              result.verifikasi_dasar = null;
              result.probabilitas = Math.min(Number(result.probabilitas) || 0, 0.45);
              result.analysis_basis = relatedArticles.length
                ? 'prediksi teks sementara; sumber terkait belum cukup untuk memastikan klaim'
                : 'prediksi teks sementara; tidak ditemukan sumber yang cukup relevan';
            }
          } catch (sourceErr) {
            console.warn('[!] Sumber terkait tidak dapat dimuat untuk analisis:', sourceErr.message);
            result.related_sources = [];
            result.verifikasi_status = 'belum terverifikasi';
            result.verifikasi_dasar = null;
            result.probabilitas = Math.min(Number(result.probabilitas) || 0, 0.45);
            result.analysis_basis = 'prediksi teks sementara; pencarian sumber gagal, hasil belum terkonfirmasi';
          }

          result.url = result.related_sources[0]?.url || submittedSourceUrl || `https://www.google.com/search?q=${encodeURIComponent(cleanSearchQuery)}`;
          result.url_type = result.related_sources.length || submittedSourceUrl ? 'source' : 'search';
        }

        // Simpan hanya satu titik jika teks menyebut lokasi. Jangan membuat
        // lokasi asal atau titik penyebar acak seolah-olah berasal dari data nyata.
        if (save && originCity && result.verifikasi_status === 'terverifikasi') {
          const originNewsUrl = result.url || '';
          const verificationRefs = (result.related_sources || []).filter(source => source.url).map(source => ({
            sumber: source.source || source.domain,
            url: source.url,
            judul: source.title
          }));

          const insertOriginQuery = `
            INSERT INTO social_threats 
            (sumber, teks, label, probabilitas, geom, lokasi_nama, url, referensi_urls,
             status_tayang, verifikasi_status, verifikasi_dasar, verifikasi_pada, dikoreksi, dibuat_pada)
            VALUES 
            ($1, $2, $3, $4, ST_SetSRID(ST_MakePoint($5, $6), 4326), $7, $8, $9::jsonb,
             TRUE, 'terverifikasi', $10, NOW(), FALSE, NOW())
            RETURNING 
              id, sumber, teks, label, probabilitas, 
              ST_AsGeoJSON(geom) AS geojson, 
              lokasi_nama, url, referensi_urls, status_tayang, verifikasi_status,
              verifikasi_dasar, verifikasi_pada, dikoreksi, dibuat_pada;
          `;
          const originRes = await pool.query(insertOriginQuery, [
            'cek_mandiri',
            result.teks,
            result.label,
            result.probabilitas,
            originCity.lon,
            originCity.lat,
            originCity.nama,
            originNewsUrl,
            JSON.stringify(verificationRefs),
            result.verifikasi_dasar || result.analysis_basis
          ]);
          const originFeature = rowToGeoJSONFeature(originRes.rows[0]);
          originFeature.properties.is_origin = true;
          savedFeatures.push(originFeature);
          io.emit('new_threat', originFeature);
          result.saved_feature = originFeature;
        } else if (save && result.verifikasi_status !== 'terverifikasi') {
          result.saved = false;
          result.saved_reason = 'Data belum disimpan karena sumbernya belum memenuhi standar verifikasi.';
        }
        result.saved_features = savedFeatures;

        res.json({ success: true, analysis: result });
      } catch (parseErr) {
        console.error('[!] Error parsing JSON hasil analisis:', parseErr);
        res.status(500).json({ error: 'Gagal memproses hasil analisis' });
      }
    }
  );
});

// Search actual publisher articles and indexed social posts for a keyword.
// Search variants include common Indonesian paraphrases, then the Python worker
// fetches and classifies the complete article/post body rather than the headline.
app.get('/api/search/news', async (req, res) => {
  const query = String(req.query.q || '').trim().slice(0, 180);
  if (query.length < 2) {
    return res.status(400).json({ success: false, error: 'Masukkan minimal 2 karakter untuk mencari berita.' });
  }

  try {
    const socialPosts = await loadRecentSocialPosts(query, 40);
    const result = await runNewsSearchWorker(query, socialPosts);
    if (!result.success) {
      return res.status(502).json({ success: false, error: result.error || 'Pencarian sumber berita gagal.' });
    }
    res.json(result);
  } catch (err) {
    console.error('[!] Pencarian berita gagal:', err.message);
    const timedOut = /batas waktu/.test(err.message);
    res.status(timedOut ? 504 : 502).json({
      success: false,
      error: timedOut ? 'Pencarian sumber terlalu lama. Coba kata kunci yang lebih singkat.' : 'Pencarian sumber berita sedang tidak tersedia. Coba lagi sebentar.'
    });
  }
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

app.get('/api/ingest/status', (req, res) => {
  res.set('Cache-Control', 'no-store');
  res.json({
    last_checked: latestIngestStatus.last_checked,
    news: latestIngestStatus.news,
    social: latestIngestStatus.social,
  });
});

// ==========================================
// DAILY VIRAL NEWS INGEST & 48-HOUR ROLLING ENGINE
// ==========================================

const SCRAPER_SCRIPT = path.join(ROOT_DIR, 'scripts', 'scraper', 'scrape_viral_daily.py');
const SOCIAL_SCRAPER_SCRIPT = path.join(ROOT_DIR, 'scripts', 'scraper', 'free_social_scraper.py');
const YTDLP_SCRAPER_SCRIPT = path.join(ROOT_DIR, 'scripts', 'scraper', 'ytdlp_social_scraper.py');
const INGEST_TIMEOUT_MS = 20 * 60 * 1000;
const INGEST_MAX_BUFFER = 16 * 1024 * 1024;

// Eksekusi scraper berita viral via Python
function executeDailyViralIngest() {
  return new Promise((resolve, reject) => {
    execFile(PYTHON_EXEC, [SCRAPER_SCRIPT], {
      cwd: ROOT_DIR,
      timeout: INGEST_TIMEOUT_MS,
      maxBuffer: INGEST_MAX_BUFFER
    }, (error, stdout, stderr) => {
      if (error) {
        const detail = String(stderr || error.message).slice(0, 4000);
        latestIngestStatus.news = {
          status: 'tidak_tersedia',
          fetched: 0,
          inserted: 0,
          verified: 0,
          withheld: 0,
          sources: {},
          error: detail
        };
        latestIngestStatus.last_checked = new Date().toISOString();
        console.error('[!] Error eksekusi scraper berita viral:', detail);
        return reject(error);
      }
      try {
        const match = stdout.match(/\{[\s\S]*"status"[\s\S]*\}/);
        const parsed = match ? JSON.parse(match[0]) : { status: 'success', raw: stdout };
        latestIngestStatus.news = {
          status: parsed.status || 'success',
          fetched: parsed.fetched || 0,
          inserted: parsed.inserted || 0,
          verified: parsed.inserted || 0,
          withheld: parsed.withheld || 0,
          sources: parsed.source_status || {},
        };
        latestIngestStatus.last_checked = new Date().toISOString();

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
                verifikasi_status: item.verifikasi_status || 'terverifikasi',
                verifikasi_dasar: item.verifikasi_dasar || null,
                dikoreksi: false,
                dibuat_pada: item.dibuat_pada
              }
            };
            io.emit('new_threat', feature);
          }
        }

        resolve(parsed);
      } catch (e) {
        const detail = `Respons scraper berita tidak dapat dibaca: ${e.message}`;
        latestIngestStatus.news = {
          status: 'error', fetched: 0, inserted: 0, verified: 0, withheld: 0,
          sources: {}, error: detail
        };
        latestIngestStatus.last_checked = new Date().toISOString();
        console.error('[!] Gagal membaca hasil scraper berita:', detail);
        resolve({ status: 'error', error: detail });
      }
    });
  });
}

// Eksekusi scraper media sosial gratis (Mastodon / Bluesky / Reddit) via Python
function executeFreeSocialIngest() {
  return new Promise((resolve) => {
    execFile(PYTHON_EXEC, [SOCIAL_SCRAPER_SCRIPT], {
      cwd: ROOT_DIR,
      timeout: INGEST_TIMEOUT_MS,
      maxBuffer: INGEST_MAX_BUFFER
    }, (error, stdout, stderr) => {
      if (error) {
        const detail = String(stderr || error.message).slice(0, 4000);
        latestIngestStatus.social = {
          status: 'tidak_tersedia',
          fetched: 0,
          inserted: 0,
          updated: 0,
          verified: {},
          withheld: {},
          sources: {},
          error: detail
        };
        latestIngestStatus.last_checked = new Date().toISOString();
        console.error('[!] Error eksekusi scraper media sosial:', detail);
        return resolve({ status: 'error', error: stderr || error.message });
      }
      try {
        const match = stdout.match(/\{[\s\S]*"status"[\s\S]*\}/);
        const parsed = match ? JSON.parse(match[0]) : { status: 'success', raw: stdout };
        latestIngestStatus.social = {
          status: parsed.status || 'success',
          fetched: parsed.fetched || 0,
          inserted: parsed.inserted || 0,
          updated: parsed.updated || 0,
          verified: parsed.verified_by_source || {},
          verified_by_label: parsed.verified_by_label || {},
          verification_summary: parsed.verification_summary || {},
          withheld: parsed.withheld_by_source || {},
          sources: parsed.source_status || {},
        };
        latestIngestStatus.last_checked = new Date().toISOString();

        for (const [source, status] of Object.entries(parsed.source_status || {})) {
          const fetched = parsed.by_source?.[source] || 0;
          const accepted = parsed.verified_by_source?.[source] || 0;
          const withheld = parsed.withheld_by_source?.[source] || 0;
          console.log(`[Social source] ${source}: status=${status.status}, fetched=${fetched}, verified=${accepted}, withheld=${withheld}${status.alasan ? `, reason=${status.alasan}` : ''}`);
        }

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
                verifikasi_status: item.verifikasi_status || 'terverifikasi',
                verifikasi_dasar: item.verifikasi_dasar || null,
                dikoreksi: false,
                dibuat_pada: item.dibuat_pada
              }
            };
            io.emit('new_threat', feature);
          }
        }

        resolve(parsed);
      } catch (e) {
        const detail = `Respons scraper media sosial tidak dapat dibaca: ${e.message}`;
        latestIngestStatus.social = {
          status: 'error', fetched: 0, inserted: 0, updated: 0,
          verified: {}, withheld: {}, sources: {}, error: detail
        };
        latestIngestStatus.last_checked = new Date().toISOString();
        console.error('[!] Gagal membaca hasil scraper media sosial:', detail);
        resolve({ status: 'error', error: detail });
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
        AND verifikasi_status IN ('terverifikasi', 'prediksi_sementara')
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
// Data >24 jam disembunyikan dari peta; data yang melewati masa simpan dihapus permanen.

async function cleanupThreatData() {
  try {
    const hideQuery = `
      UPDATE social_threats 
      SET status_tayang = FALSE 
      WHERE status_tayang = TRUE 
        AND dibuat_pada < (NOW() - INTERVAL '24 hours');
    `;
    const hidden = await pool.query(hideQuery);
    if (hidden.rowCount > 0) {
      console.log(`[🧹 Pembersihan Otomatis] ${hidden.rowCount} berita yang lewat 24 jam disembunyikan dari peta.`);
      io.emit('threats_purged', { count: hidden.rowCount, message: 'Data yang telah lewat 24 jam otomatis dihilangkan dari peta.' });
    }

    const deleted = await pool.query(
      `DELETE FROM social_threats WHERE dibuat_pada < NOW() - make_interval(days => $1::int);`,
      [DATA_RETENTION_DAYS]
    );
    if (deleted.rowCount > 0) {
      console.log(`[🗑️ Penghapusan Otomatis] ${deleted.rowCount} berita berusia lebih dari ${DATA_RETENTION_DAYS} hari dihapus permanen.`);
    }
  } catch (err) {
    console.error('[!] Error purge data kadaluarsa:', err.message);
  }
}

let isAutonomousIngesting = false;
let verificationSchemaReady = false;
const latestIngestStatus = {
  last_checked: null,
  news: { status: 'belum_diperiksa', sources: {} },
  social: { status: 'belum_diperiksa', verified_by_label: {}, verification_summary: {}, sources: {} },
};
async function runHourlyViralIngestWorker() {
  if (!verificationSchemaReady) return;
  if (isAutonomousIngesting) return;
  isAutonomousIngesting = true;
  try {
    console.log(`\n[🕒 Hourly Ingest Worker] Menjalankan pengecekan berkala berita viral terbaru...`);
    
    // 1. Sembunyikan data >24 jam dan hapus data yang melewati masa simpan
    await cleanupThreatData();

    // 2. Tarik berita viral terkini (Google News, TurnBackHoax, Antara, CNBC, TikTok, Instagram)
    try {
      const result = await executeDailyViralIngest();
      if (result?.status === 'error') {
        console.error('[!] Ingest portal tidak berhasil:', result.error);
      } else if (result && result.inserted > 0) {
        console.log(`[⚡ Realtime Auto Ingest] +${result.inserted} berita baru disiarkan ke peta Leaflet!`);
        io.emit('viral_synced', { timestamp: new Date().toISOString(), result });
      } else {
        console.log(`[✓ Ingest portal selesai: fetched=${result?.fetched || 0}, withheld=${result?.withheld || 0}]`);
      }
    } catch (newsErr) {
      console.error('[!] Error pada ingest portal; pengambilan sosial media tetap dilanjutkan:', newsErr.message);
    }

    // 3. Tarik media sosial gratis (Mastodon, Bluesky, Reddit) tanpa API key
    try {
      const socialResult = await executeFreeSocialIngest();
      if (socialResult?.status === 'error') {
        console.error('[!] Ingest media sosial tidak berhasil:', socialResult.error);
      } else if (socialResult && socialResult.inserted > 0) {
        console.log(`[⚡ Social Auto Ingest] +${socialResult.inserted} postingan media sosial baru disiarkan ke peta Leaflet!`);
        io.emit('social_synced', {
          timestamp: new Date().toISOString(),
          result: socialResult
        });
      } else {
        console.log(`[✓ Social Ingest Selesai: fetched=${socialResult?.fetched || 0}, inserted=${socialResult?.inserted || 0}]`);
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

// Jalankan otomatis tiap satu jam; penjaga worker mencegah proses bertumpuk.
setInterval(runHourlyViralIngestWorker, 3600000);

// Evaluasi pembersihan data >24 jam setiap 5 menit
setInterval(cleanupThreatData, 300000);

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

ensureVerificationSchema()
  .then(() => {
    verificationSchemaReady = true;
    server.listen(PORT, () => {
      console.log(`
======================================================
  🛡️  GEOSENTRY BACKEND & WEBSOCKET ENGINE ONLINE
  🌐  URL Server   : http://localhost:${PORT}
  📍  PostGIS Mode : ST_AsGeoJSON Native (Active)
  🔎  Evidence Gate: active before map display
  ⚡  WebSockets   : Socket.io Ready
======================================================
      `);

      // Start only after the database verification schema is ready and the
      // server is listening, so a slow database startup cannot skip the first
      // scheduled ingest for an entire hour.
      setTimeout(() => {
        runHourlyViralIngestWorker().catch((err) => {
          console.error('[!] Sinkronisasi awal gagal:', err.message);
        });
      }, 3000);
    });
  })
  .catch((err) => {
    console.error('[!] Gagal menyiapkan gerbang verifikasi database:', err.message);
    process.exitCode = 1;
  });
