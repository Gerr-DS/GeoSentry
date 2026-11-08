/**
 * GeoSentry - Frontend WebGIS Client Application
 * Menghubungkan Leaflet.js, WebSockets (Socket.io), Chart.js, dan REST API.
 * Fitur:
 * - Basemap resmi bebas API key & tanpa watermark (ESRI Dark Gray + OpenStreetMap)
 * - Live Search / Pencarian berita real-time
 * - Cek Berita Mandiri dengan AI (Instant Fact-Checker)
 * - Feedback loop analis langsung dari peta
 */

// State Global Aplikasi
const AppState = {
  threats: new Map(), // id -> feature
  markers: new Map(), // id -> L.marker
  currentFilter: { label: 'all', sumber: 'all', search: '' },
  activeLayer: 'markers', // 'markers' | 'heatmap'
  basemapMode: 'esri', // 'esri' | 'osm_dark' | 'osm_light'
  map: null,
  esriDarkTile: null,
  osmTile: null,
  heatLayer: null,
  chartCategory: null,
  chartRegions: null,
  socket: null,
  lastAnalysis: null
};

// ==========================================
// 1. INISIALISASI PETA LEAFLET (BEBAS WATERMARK & API KEY)
// ==========================================
function initMap() {
  const mapContainer = document.getElementById('map');
  if (!mapContainer) return;

  // Inisialisasi Peta Leaflet terpusat di Kepulauan Indonesia
  AppState.map = L.map('map', {
    zoomControl: false,
    minZoom: 4,
    maxZoom: 18,
    preferCanvas: true
  }).setView([-2.2, 118.0], 5);

  // Zoom control di pojok kanan bawah
  L.control.zoom({ position: 'bottomright' }).addTo(AppState.map);

  // Layer 1: ESRI World Dark Gray Canvas (Resmi, 100% Bebas API Key, Tanpa Watermark)
  AppState.esriDarkTile = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}', {
    attribution: '&copy; Esri, DeLorme, NAVTEQ',
    maxZoom: 16
  });

  // Layer 2: OpenStreetMap Standard (100% Free Open Data)
  AppState.osmTile = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
    attribution: '&copy; OpenStreetMap contributors',
    maxZoom: 19
  });

  // Pasang default ESRI Dark Canvas (Bersih dari tulisan 'API KEY REQUIRED')
  AppState.esriDarkTile.addTo(AppState.map);

  // Inisialisasi Heat Layer
  AppState.heatLayer = L.heatLayer([], {
    radius: 30,
    blur: 22,
    maxZoom: 12,
    gradient: {
      0.2: '#3b82f6',
      0.5: '#f59e0b',
      0.8: '#ef4444'
    }
  });

  // Force Leaflet untuk menghitung ulang ukuran kontainer agar peta tampil penuh
  setTimeout(() => { if (AppState.map) AppState.map.invalidateSize(); }, 50);
  setTimeout(() => { if (AppState.map) AppState.map.invalidateSize(); }, 250);
  setTimeout(() => { if (AppState.map) AppState.map.invalidateSize(); }, 600);

  window.addEventListener('resize', () => {
    if (AppState.map) AppState.map.invalidateSize();
  });

  AppState.map.on('popupopen', () => {
    if (window.lucide && typeof lucide.createIcons === 'function') {
      lucide.createIcons();
    }
  });
}

// Helper: Custom Marker Icon dengan Efek Radar Berdenyut
function createCustomIcon(label) {
  const markerClass = label === 'hoaks' ? 'marker-hoaks' : (label === 'fakta' ? 'marker-fakta' : 'marker-negatif');
  return L.divIcon({
    className: 'custom-leaflet-pin',
    html: `<div class="marker-pin ${markerClass}"></div>`,
    iconSize: [20, 20],
    iconAnchor: [10, 10],
    popupAnchor: [0, -10]
  });
}

// Helper: Dapatkan Link Berita Terkait yang Valid
function getRelatedNewsUrl(props) {
  if (props.url && (props.url.startsWith('http://') || props.url.startsWith('https://')) && !props.url.includes('geosentry.id')) {
    return props.url;
  }
  let cleanQuery = (props.teks || '')
    .replace(/^\[.*?\]\s*/, '')
    .replace(/^(Viral|Awas|Waspada|Kabar heboh|Informasi A1|Bongkar|Klarifikasi)[:!]\s*/i, '')
    .replace(/["'”]/g, '')
    .slice(0, 75)
    .trim();

  if (!cleanQuery) cleanQuery = props.lokasi_nama || 'berita Indonesia';

  if (props.label === 'hoaks') {
    return `https://turnbackhoax.id/?s=${encodeURIComponent(cleanQuery)}`;
  } else if (props.sumber === 'twitter') {
    return `https://twitter.com/search?q=${encodeURIComponent(cleanQuery)}&f=live`;
  } else {
    return `https://news.google.com/search?q=${encodeURIComponent(cleanQuery)}&hl=id&gl=ID&ceid=ID:id`;
  }
}

// Helper: Format Popup Interaktif & Tombol Feedback Loop
function createPopupContent(props) {
  const labelColor = props.label === 'hoaks' ? 'text-red-400 bg-red-950/60 border-red-800' : 
                    (props.label === 'fakta' ? 'text-blue-400 bg-blue-950/60 border-blue-800' : 'text-amber-400 bg-amber-950/60 border-amber-800');
  
  const probPercent = Math.round((props.probabilitas || 0.5) * 100);
  const timeStr = new Date(props.dibuat_pada || Date.now()).toLocaleTimeString('id-ID');

  let sumberLabel = '📰 Berita';
  if (props.sumber === 'twitter') sumberLabel = '🐦 Twitter';
  else if (props.sumber === 'cek_mandiri') sumberLabel = '✨ Cek Mandiri';

  const newsUrl = getRelatedNewsUrl(props);
  let linkIcon = 'newspaper';
  let linkTitle = 'Baca Berita di Google News';
  if (props.label === 'hoaks') {
    linkIcon = 'shield-alert';
    linkTitle = 'Cek Fakta di TurnBackHoax';
  } else if (props.sumber === 'twitter') {
    linkIcon = 'message-circle';
    linkTitle = 'Lihat di Twitter / X';
  } else if (props.label === 'negatif') {
    linkIcon = 'alert-triangle';
    linkTitle = 'Pantau Berita & Isu Terkait';
  }

  return `
    <div class="text-xs space-y-2.5 min-w-[260px] max-w-[320px]">
      <div class="flex items-center justify-between border-b border-slate-700/60 pb-1.5">
        <span class="px-2 py-0.5 rounded text-[10px] font-bold uppercase border ${labelColor}">
          ${props.label}
        </span>
        <span class="text-[10px] font-mono text-slate-400">
          ${sumberLabel} &bull; ${timeStr}
        </span>
      </div>

      <p class="text-slate-200 text-xs font-normal leading-relaxed line-clamp-3">
        "${props.teks}"
      </p>

      <div class="space-y-1">
        <div class="flex justify-between text-[10px] text-slate-400">
          <span>Keyakinan AI (SGDClassifier):</span>
          <span class="font-mono font-bold text-slate-200">${probPercent}%</span>
        </div>
        <div class="w-full bg-slate-800 rounded-full h-1.5 overflow-hidden">
          <div class="h-1.5 rounded-full ${props.label === 'hoaks' ? 'bg-red-500' : (props.label === 'fakta' ? 'bg-blue-500' : 'bg-amber-500')}" style="width: ${probPercent}%"></div>
        </div>
      </div>

      <div class="flex items-center text-[11px] text-slate-400">
        <span class="mr-1">📍</span> Lokasi: <strong class="text-slate-200 ml-1">${props.lokasi_nama || 'Nasional'}</strong>
      </div>

      <!-- Tautan Berita Terkait yang Dapat Diklik Langsung -->
      <div class="pt-1.5 border-t border-slate-700/60">
        <a href="${newsUrl}" target="_blank" rel="noopener noreferrer" 
           class="flex items-center justify-between px-2.5 py-1.5 rounded-lg bg-cyan-950/80 hover:bg-cyan-900 border border-cyan-700/60 text-cyan-200 hover:text-white transition group text-[11px] font-semibold">
          <span class="flex items-center space-x-1.5 truncate mr-1">
            <i data-lucide="${linkIcon}" class="w-3.5 h-3.5 text-cyan-400 group-hover:scale-110 transition shrink-0"></i>
            <span class="truncate max-w-[180px]">${linkTitle}</span>
          </span>
          <span class="text-[10px] text-cyan-400 font-mono flex items-center shrink-0">
            <span>Buka</span> &rarr;
          </span>
        </a>
      </div>

      <!-- Feedback Loop Koreksi Analis -->
      <div class="pt-1.5 border-t border-slate-700/60 flex items-center justify-between gap-1.5">
        <span class="text-[10px] text-emerald-400 font-semibold flex items-center">
          <span class="w-1.5 h-1.5 rounded-full bg-emerald-400 mr-1 animate-pulse"></span> Feedback:
        </span>
        <div class="flex items-center space-x-1">
          <button onclick="submitCorrection(${props.id}, 'fakta')" class="px-2 py-1 rounded bg-blue-600 hover:bg-blue-500 text-white text-[10px] font-bold transition">
            Fakta
          </button>
          <button onclick="submitCorrection(${props.id}, 'hoaks')" class="px-2 py-1 rounded bg-red-600 hover:bg-red-500 text-white text-[10px] font-bold transition">
            Hoaks
          </button>
          <button onclick="submitCorrection(${props.id}, 'negatif')" class="px-2 py-1 rounded bg-amber-600 hover:bg-amber-500 text-white text-[10px] font-bold transition">
            Negatif
          </button>
        </div>
      </div>
    </div>
  `;
}

// ==========================================
// 2. LOGIKA DATA, FILTER & PENCARIAN
// ==========================================

function addThreatToMap(feature, autoFly = false) {
  const id = feature.properties.id;
  AppState.threats.set(id, feature);

  if (!feature.geometry || !feature.geometry.coordinates) return;
  const [lon, lat] = feature.geometry.coordinates;

  if (AppState.markers.has(id)) {
    const existing = AppState.markers.get(id);
    existing.setIcon(createCustomIcon(feature.properties.label));
    existing.setPopupContent(createPopupContent(feature.properties));
    return;
  }

  const marker = L.marker([lat, lon], {
    icon: createCustomIcon(feature.properties.label)
  }).bindPopup(createPopupContent(feature.properties));

  AppState.markers.set(id, marker);

  if (isFeatureMatchFilter(feature) && AppState.activeLayer === 'markers') {
    marker.addTo(AppState.map);
  }

  if (autoFly) {
    AppState.map.flyTo([lat, lon], Math.max(AppState.map.getZoom(), 7), {
      duration: 1.2
    });
    setTimeout(() => marker.openPopup(), 1200);
  }

  updateHeatmapData();
  prependThreatToFeed(feature.properties, true);
}

function isFeatureMatchFilter(feature) {
  const { label, sumber, search } = AppState.currentFilter;
  const p = feature.properties;

  const matchLabel = label === 'all' || p.label === label;
  const matchSumber = sumber === 'all' || p.sumber === sumber;
  
  let matchSearch = true;
  if (search && search.trim() !== '') {
    const q = search.toLowerCase().trim();
    const textMatch = (p.teks || '').toLowerCase().includes(q);
    const locMatch = (p.lokasi_nama || '').toLowerCase().includes(q);
    const labelMatch = (p.label || '').toLowerCase().includes(q);
    matchSearch = textMatch || locMatch || labelMatch;
  }

  return matchLabel && matchSumber && matchSearch;
}

function applyFilters() {
  AppState.markers.forEach((marker, id) => {
    const feature = AppState.threats.get(id);
    if (feature && isFeatureMatchFilter(feature) && AppState.activeLayer === 'markers') {
      if (!AppState.map.hasLayer(marker)) marker.addTo(AppState.map);
    } else {
      if (AppState.map.hasLayer(marker)) AppState.map.removeLayer(marker);
    }
  });

  updateHeatmapData();
  renderFeedList();
}

function updateHeatmapData() {
  const heatPoints = [];
  AppState.threats.forEach((feature) => {
    if (isFeatureMatchFilter(feature) && feature.geometry && feature.geometry.coordinates) {
      const [lon, lat] = feature.geometry.coordinates;
      const weight = feature.properties.label === 'hoaks' ? 0.9 : 
                    (feature.properties.label === 'negatif' ? 0.7 : 0.4);
      heatPoints.push([lat, lon, weight]);
    }
  });
  AppState.heatLayer.setLatLngs(heatPoints);
}

// ==========================================
// 3. CHARTS & ANALITIK
// ==========================================

function initCharts() {
  const ctxCat = document.getElementById('chart-category').getContext('2d');
  AppState.chartCategory = new Chart(ctxCat, {
    type: 'doughnut',
    data: {
      labels: ['Hoaks', 'Fakta', 'Negatif'],
      datasets: [{
        data: [0, 0, 0],
        backgroundColor: ['#ef4444', '#3b82f6', '#f59e0b'],
        borderWidth: 0,
        hoverOffset: 6
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      cutout: '70%',
      plugins: {
        legend: {
          position: 'right',
          labels: { color: '#cbd5e1', font: { size: 10, family: 'Plus Jakarta Sans' }, boxWidth: 10 }
        }
      }
    }
  });

  const ctxReg = document.getElementById('chart-regions').getContext('2d');
  AppState.chartRegions = new Chart(ctxReg, {
    type: 'bar',
    data: {
      labels: [],
      datasets: [{
        label: 'Jumlah Ancaman',
        data: [],
        backgroundColor: '#06b6d4',
        borderRadius: 4
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      indexAxis: 'y',
      scales: {
        x: { grid: { color: 'rgba(51, 65, 85, 0.4)' }, ticks: { color: '#94a3b8', font: { size: 9 } } },
        y: { grid: { display: false }, ticks: { color: '#e2e8f0', font: { size: 10 } } }
      },
      plugins: {
        legend: { display: false }
      }
    }
  });
}

function updateChartsAndStats(stats) {
  const totalActive = stats.total_active || 0;
  document.getElementById('stat-total').innerText = totalActive;
  document.getElementById('stat-hoaks').innerText = stats.hoaks || 0;
  document.getElementById('stat-fakta').innerText = stats.fakta || 0;
  document.getElementById('stat-negatif').innerText = stats.negatif || 0;

  const viralActiveEl = document.getElementById('viral-active-count');
  if (viralActiveEl) viralActiveEl.innerText = totalActive;

  const rangeEl = document.getElementById('window-range-text');
  if (rangeEl && (stats.hari_ini_count !== undefined || stats.kemarin_count !== undefined)) {
    rangeEl.innerText = `Hari Ini (${stats.hari_ini_count || 0}) & Kemarin (${stats.kemarin_count || 0})`;
  }

  if (AppState.chartCategory) {
    AppState.chartCategory.data.datasets[0].data = [
      stats.hoaks || 0,
      stats.fakta || 0,
      stats.negatif || 0
    ];
    AppState.chartCategory.update();
  }

  if (AppState.chartRegions && stats.top_regions) {
    AppState.chartRegions.data.labels = stats.top_regions.map(r => r.lokasi_nama);
    AppState.chartRegions.data.datasets[0].data = stats.top_regions.map(r => parseInt(r.count, 10));
    AppState.chartRegions.update();
  }
}

// ==========================================
// 4. FEED RADAR TICKER
// ==========================================

function prependThreatToFeed(props, isNew = false) {
  const container = document.getElementById('feed-container');
  const emptyMsg = document.getElementById('empty-feed-msg');
  if (emptyMsg) emptyMsg.remove();

  const labelColor = props.label === 'hoaks' ? 'border-l-red-500 text-red-400' : 
                    (props.label === 'fakta' ? 'border-l-blue-500 text-blue-400' : 'border-l-amber-500 text-amber-400');
  
  const div = document.createElement('div');
  div.id = `feed-item-${props.id}`;
  div.className = `p-2.5 rounded-lg bg-slate-900/90 border border-slate-800/80 border-l-4 ${labelColor} text-xs transition cursor-pointer hover:bg-slate-800/80 ${isNew ? 'threat-item-new' : ''}`;
  
  div.onclick = () => {
    const marker = AppState.markers.get(props.id);
    if (marker) {
      AppState.map.flyTo(marker.getLatLng(), 8, { duration: 1 });
      setTimeout(() => marker.openPopup(), 1000);
    }
  };

  let sumberIcon = '📰 Berita';
  if (props.sumber === 'twitter') sumberIcon = '🐦 Twitter';
  else if (props.sumber === 'cek_mandiri') sumberIcon = '✨ Cek Mandiri';

  const newsUrl = getRelatedNewsUrl(props);
  const linkBadgeText = props.label === 'hoaks' ? 'Cek Fakta' : (props.sumber === 'twitter' ? 'Twitter' : 'Berita');

  div.innerHTML = `
    <div class="flex items-center justify-between mb-1">
      <span class="font-bold uppercase text-[10px] tracking-wider">${props.label}</span>
      <span class="text-[10px] text-slate-400 font-mono">📍 ${props.lokasi_nama || 'Nasional'}</span>
    </div>
    <p class="text-slate-300 line-clamp-2 leading-relaxed">
      ${props.teks}
    </p>
    <div class="mt-1.5 flex items-center justify-between text-[10px] text-slate-400">
      <span class="flex items-center space-x-1">${sumberIcon}</span>
      <a href="${newsUrl}" target="_blank" rel="noopener noreferrer" 
         onclick="event.stopPropagation();" 
         class="text-cyan-400 hover:text-cyan-200 transition flex items-center space-x-1 hover:underline font-semibold bg-cyan-950/70 px-1.5 py-0.5 rounded border border-cyan-800/50">
        <span>${linkBadgeText}</span>
        <i data-lucide="external-link" class="w-2.5 h-2.5 ml-0.5"></i>
      </a>
      <span class="font-mono text-slate-500">Conf: ${Math.round((props.probabilitas || 0.5) * 100)}%</span>
    </div>
  `;

  container.prepend(div);
  updateFeedCountBadge();
  if (isNew && window.lucide && typeof lucide.createIcons === 'function') {
    lucide.createIcons();
  }
}

function renderFeedList() {
  const container = document.getElementById('feed-container');
  container.innerHTML = '';
  
  const filtered = Array.from(AppState.threats.values()).filter(isFeatureMatchFilter);
  if (filtered.length === 0) {
    container.innerHTML = `
      <div id="empty-feed-msg" class="text-center py-10 text-slate-500 text-xs">
        <i data-lucide="satellite" class="w-8 h-8 mx-auto mb-2 opacity-40"></i>
        Tidak ada data ancaman yang cocok dengan pencarian / filter.
      </div>
    `;
    lucide.createIcons();
    return;
  }

  filtered.slice(0, 50).forEach(feat => {
    prependThreatToFeed(feat.properties, false);
  });
  updateFeedCountBadge();
  if (window.lucide && typeof lucide.createIcons === 'function') {
    lucide.createIcons();
  }
}

function updateFeedCountBadge() {
  const count = Array.from(AppState.threats.values()).filter(isFeatureMatchFilter).length;
  document.getElementById('feed-count-badge').innerText = `${count} item`;
}

// ==========================================
// 5. FEEDBACK LOOP & CEK BERITA MANDIRI
// ==========================================

async function submitCorrection(id, newLabel) {
  try {
    const res = await fetch(`/api/threats/${id}/correct`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ label: newLabel })
    });
    const data = await res.json();
    if (data.success) {
      console.log(`[✓] Berhasil mengoreksi ID ${id} ke ${newLabel}`);
      fetchStats();
    }
  } catch (err) {
    console.error('[!] Gagal submit koreksi:', err);
  }
}

// Cek Berita Mandiri dengan AI & Pelacakan Sebaran Viralitas Multi-Titik
async function runNewsAnalysis(saveToMap = true) {
  const textInput = document.getElementById('checker-input-text');
  const text = textInput ? textInput.value.trim() : '';

  if (!text) {
    alert('Silakan masukkan teks narasi atau judul berita terlebih dahulu!');
    return;
  }

  const btnPlotText = document.getElementById('btn-plot-text');
  if (btnPlotText) btnPlotText.innerText = '⚡ AI Sedang Menganalisis & Melacak Sebaran...';

  try {
    const res = await fetch('/api/analyze', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text, save: saveToMap })
    });
    const data = await res.json();
    if (btnPlotText) btnPlotText.innerText = '⚡ Analisis AI & Lacak Titik Sebaran di Peta 🚀';

    if (data.success && data.analysis) {
      const a = data.analysis;
      AppState.lastAnalysis = a;

      // Tampilkan Result Box di Modal
      const resultBox = document.getElementById('checker-result-box');
      if (resultBox) resultBox.classList.remove('hidden');

      // Update Badge Klasifikasi
      const badge = document.getElementById('res-badge');
      if (badge) {
        if (a.label === 'hoaks') {
          badge.className = 'px-2.5 py-0.5 rounded text-[11px] font-bold uppercase bg-red-950 text-red-400 border border-red-800';
          badge.innerText = '🔴 TERDETEKSI HOAKS';
        } else if (a.label === 'fakta') {
          badge.className = 'px-2.5 py-0.5 rounded text-[11px] font-bold uppercase bg-blue-950 text-blue-400 border border-blue-800';
          badge.innerText = '🔵 FAKTA / BERITA VALID';
        } else {
          badge.className = 'px-2.5 py-0.5 rounded text-[11px] font-bold uppercase bg-amber-950 text-amber-400 border border-amber-800';
          badge.innerText = '🟡 SENTIMEN NEGATIF / PROVOKASI';
        }
      }

      // Update Confidence Bar
      const confPercent = Math.round(a.probabilitas * 100);
      const confEl = document.getElementById('res-confidence');
      if (confEl) confEl.innerText = `${confPercent}%`;
      const bar = document.getElementById('res-prob-bar');
      if (bar) {
        bar.style.width = `${confPercent}%`;
        bar.className = `h-2 rounded-full transition-all duration-500 ${a.label === 'hoaks' ? 'bg-red-500' : (a.label === 'fakta' ? 'bg-blue-500' : 'bg-amber-500')}`;
      }

      // Update Info Episentrum Pembuat (Origin)
      const originName = a.origin ? a.origin.nama : (a.lokasi ? a.lokasi.nama : 'Nasional');
      const originEl = document.getElementById('res-origin');
      if (originEl) originEl.innerText = originName;

      // Update Info Titik-Titik Penyebar (Spreaders)
      const spreadersList = document.getElementById('res-spreaders-list');
      if (spreadersList && a.spreaders && a.spreaders.length > 0) {
        spreadersList.innerHTML = a.spreaders.map(s => {
          const cName = s.nama.split('(')[0].trim();
          return `<span class="px-2 py-0.5 rounded bg-slate-800 text-cyan-300 border border-slate-700 font-mono text-[10px]">📍 ${cName}</span>`;
        }).join('');
      }

      // Update Link Rujukan Berita Terkait Langsung
      const newsLinkEl = document.getElementById('res-news-link');
      const newsLinkLabel = document.getElementById('res-news-link-label');
      const newsUrl = a.url || getRelatedNewsUrl({ teks: a.teks, label: a.label, sumber: 'cek_mandiri' });
      if (newsLinkEl && newsUrl) {
        newsLinkEl.href = newsUrl;
        if (newsLinkLabel) {
          newsLinkLabel.innerText = a.label === 'hoaks' 
            ? 'Cek Fakta di TurnBackHoax ↗' 
            : (a.label === 'fakta' ? 'Buka Berita di Google News ↗' : 'Pantau Isu Terkait ↗');
        }
      }
      if (window.lucide && typeof lucide.createIcons === 'function') {
        lucide.createIcons();
      }

      // PEMETAAN MULTI-TITIK DI PETA LEAFLET (Origin + 3-5 Penyebar)
      const featuresToPlot = a.saved_features || (a.saved_feature ? [a.saved_feature] : []);
      if (featuresToPlot.length > 0) {
        // Plot setiap titik ke peta
        featuresToPlot.forEach(feat => {
          addThreatToMap(feat, false);
        });

        // Hitung batas geografis (LatLngBounds) yang mencakup seluruh titik origin + spreader
        const bounds = L.latLngBounds();
        let originCoord = null;
        let originId = null;

        featuresToPlot.forEach(feat => {
          if (feat.geometry && feat.geometry.coordinates) {
            const [lon, lat] = feat.geometry.coordinates;
            bounds.extend([lat, lon]);
            if (feat.properties && feat.properties.sumber === 'cek_mandiri') {
              originCoord = [lat, lon];
              originId = feat.properties.id;
            }
          }
        });

        // Fallback jika originId belum ketemu
        if (!originId && featuresToPlot[0] && featuresToPlot[0].properties) {
          originId = featuresToPlot[0].properties.id;
          const [lon, lat] = featuresToPlot[0].geometry.coordinates;
          originCoord = [lat, lon];
        }

        // Beri jeda 2 detik agar pengguna dapat melihat klasifikasi & link rujukan sebelum fokus ke peta
        setTimeout(() => {
          const modalEl = document.getElementById('modal-checker');
          if (modalEl) modalEl.classList.add('hidden');
          if (textInput) textInput.value = '';

          // Arahkan kamera peta ke seluruh titik sebaran di Indonesia
          if (bounds.isValid()) {
            AppState.map.fitBounds(bounds, { padding: [60, 60], maxZoom: 8, duration: 1.5 });
          }

          // Buka popup pada episentrum pembuat
          setTimeout(() => {
            if (originId && AppState.markers.has(originId)) {
              AppState.markers.get(originId).openPopup();
            }
          }, 1200);
        }, 2000);

        // Update statistik & grafik wilayah terpanas secara langsung
        fetchStats();
      }
    } else {
      alert(data.error || 'Gagal menganalisis teks.');
    }
  } catch (err) {
    if (btnPlotText) btnPlotText.innerText = '⚡ Analisis AI & Lacak Titik Sebaran di Peta 🚀';
    console.error('[!] Error saat menganalisis berita:', err);
    alert('Terjadi kesalahan koneksi saat menganalisis berita.');
  }
}

// Memuat daftar kota dari backend untuk autocomplete
async function loadCitiesDatalist() {
  try {
    const res = await fetch('/api/cities');
    const cities = await res.json();
    AppState.cityList = cities;
    const datalist = document.getElementById('cities-datalist');
    if (!datalist) return;
    datalist.innerHTML = '';
    cities.forEach(c => {
      const opt = document.createElement('option');
      const capName = c.nama.charAt(0).toUpperCase() + c.nama.slice(1);
      opt.value = `${capName} (${c.provinsi})`;
      datalist.appendChild(opt);
    });
  } catch (err) {
    console.error('[!] Gagal memuat datalist kota:', err);
  }
}

// ==========================================
// 6. WEBSOCKET REAL-TIME EVENTS
// ==========================================

function initWebSocket() {
  AppState.socket = io();

  const wsIndicator = document.getElementById('ws-indicator');
  const wsStatus = document.getElementById('ws-status');

  AppState.socket.on('connect', () => {
    wsIndicator.className = 'w-2 h-2 rounded-full bg-emerald-500 animate-pulse';
    wsStatus.className = 'text-[10px] text-emerald-400 font-medium';
    wsStatus.innerText = 'Socket Live';
  });

  AppState.socket.on('disconnect', () => {
    wsIndicator.className = 'w-2 h-2 rounded-full bg-red-500';
    wsStatus.className = 'text-[10px] text-red-400 font-medium';
    wsStatus.innerText = 'Terputus';
  });

  AppState.socket.on('new_threat', (feature) => {
    const autoFly = feature.properties.sumber === 'cek_mandiri';
    addThreatToMap(feature, autoFly);
    fetchStats();
  });

  AppState.socket.on('threat_updated', (feature) => {
    addThreatToMap(feature, false);
    renderFeedList();
    fetchStats();
  });

  // Event saat sinkronisasi berita viral harian selesai
  AppState.socket.on('viral_synced', (data) => {
    console.log('[+] Berita viral baru tersinkronkan:', data);
    fetchInitialThreats();
    fetchStats();
    showToast(`Berita viral harian disinkronkan (+${data.result?.inserted || 0} baru).`);
  });

  // Event saat pergantian hari (00:00 WIB Rollover)
  AppState.socket.on('day_rollover', (data) => {
    console.log('[🔔] Pukul 00:00 WIB: Rollover jendela 48 jam:', data);
    // Bersihkan marker lama
    AppState.threats.clear();
    AppState.markers.forEach(m => AppState.map.removeLayer(m));
    AppState.markers.clear();
    updateHeatmapData();
    renderFeedList();
    fetchInitialThreats();
    fetchStats();
    showToast(data.message || 'Pukul 00:00 WIB: Jendela pemantauan bergeser ke hari ini dan kemarin.');
  });

  AppState.socket.on('viral_engine_ready', (info) => {
    console.log('[✓]', info.message, info.rollover_schedule);
  });
}

// ==========================================
// 7. REST API FETCHERS
// ==========================================

async function fetchInitialThreats() {
  try {
    const res = await fetch('/api/threats');
    const data = await res.json();
    if (data.features) {
      data.features.forEach(feat => addThreatToMap(feat, false));
      renderFeedList();
    }
  } catch (err) {
    console.error('[!] Gagal memuat data ancaman awal:', err);
  }
}

async function fetchStats() {
  try {
    const res = await fetch('/api/stats');
    const stats = await res.json();
    updateChartsAndStats(stats);
  } catch (err) {
    console.error('[!] Gagal memuat statistik:', err);
  }
}

// ==========================================
// 8. EVENT LISTENERS UI & PENCARIAN
// ==========================================

function initUIEvents() {
  // Live Clock
  setInterval(() => {
    const now = new Date();
    document.getElementById('live-clock').innerText = now.toLocaleTimeString('id-ID') + ' WIB';
  }, 1000);

  // SEARCH BAR EVENT LISTENER (Real-time live filtering)
  const inputSearch = document.getElementById('input-search');
  const btnClearSearch = document.getElementById('btn-clear-search');

  inputSearch.addEventListener('input', (e) => {
    const query = e.target.value;
    AppState.currentFilter.search = query;
    if (query.length > 0) {
      btnClearSearch.classList.remove('hidden');
    } else {
      btnClearSearch.classList.add('hidden');
    }
    applyFilters();
  });

  btnClearSearch.addEventListener('click', () => {
    inputSearch.value = '';
    AppState.currentFilter.search = '';
    btnClearSearch.classList.add('hidden');
    applyFilters();
  });

  // Jika tekan Enter di search bar dan tidak ada hasil, tawarkan cek AI
  inputSearch.addEventListener('keypress', (e) => {
    if (e.key === 'Enter') {
      const q = inputSearch.value.trim();
      if (q) {
        const matches = Array.from(AppState.threats.values()).filter(isFeatureMatchFilter);
        if (matches.length === 0) {
          document.getElementById('modal-checker').classList.remove('hidden');
          document.getElementById('checker-input-text').value = q;
          runNewsAnalysis(false);
        }
      }
    }
  });

  // Filter Kategori & Sumber
  document.getElementById('filter-label').onchange = (e) => {
    AppState.currentFilter.label = e.target.value;
    applyFilters();
  };
  document.getElementById('filter-sumber').onchange = (e) => {
    AppState.currentFilter.sumber = e.target.value;
    applyFilters();
  };
  document.getElementById('btn-refresh').onclick = () => {
    fetchInitialThreats();
    fetchStats();
    if (AppState.map) AppState.map.invalidateSize();
  };

  // Switcher Basemap Bebas API Key
  const btnToggleBasemap = document.getElementById('btn-toggle-basemap');
  const basemapText = document.getElementById('basemap-mode-text');
  const mapEl = document.getElementById('map');

  btnToggleBasemap.onclick = () => {
    if (AppState.basemapMode === 'esri') {
      // Pindah ke OSM Dark Filter (bebas API key, filter gelap CSS)
      AppState.map.removeLayer(AppState.esriDarkTile);
      AppState.osmTile.addTo(AppState.map);
      mapEl.classList.add('osm-dark-tiles');
      AppState.basemapMode = 'osm_dark';
      basemapText.innerText = 'OSM Dark';
    } else if (AppState.basemapMode === 'osm_dark') {
      // Pindah ke OSM Standard Light
      mapEl.classList.remove('osm-dark-tiles');
      AppState.basemapMode = 'osm_light';
      basemapText.innerText = 'OSM Light';
    } else {
      // Kembali ke ESRI World Dark Canvas
      AppState.map.removeLayer(AppState.osmTile);
      AppState.esriDarkTile.addTo(AppState.map);
      mapEl.classList.remove('osm-dark-tiles');
      AppState.basemapMode = 'esri';
      basemapText.innerText = 'Dark Ops';
    }
    AppState.map.invalidateSize();
  };

  // Layer Switcher: Markers vs Heatmap
  const btnMarkers = document.getElementById('layer-markers');
  const btnHeatmap = document.getElementById('layer-heatmap');

  btnMarkers.onclick = () => {
    AppState.activeLayer = 'markers';
    btnMarkers.className = 'px-3 py-1.5 rounded-lg bg-cyan-600 text-white font-semibold transition hover:bg-cyan-500 flex items-center space-x-1.5';
    btnHeatmap.className = 'px-3 py-1.5 rounded-lg bg-slate-800 text-slate-300 font-semibold transition hover:bg-slate-700 flex items-center space-x-1.5';
    
    if (AppState.map.hasLayer(AppState.heatLayer)) AppState.map.removeLayer(AppState.heatLayer);
    applyFilters();
  };

  btnHeatmap.onclick = () => {
    AppState.activeLayer = 'heatmap';
    btnHeatmap.className = 'px-3 py-1.5 rounded-lg bg-orange-600 text-white font-semibold transition hover:bg-orange-500 flex items-center space-x-1.5';
    btnMarkers.className = 'px-3 py-1.5 rounded-lg bg-slate-800 text-slate-300 font-semibold transition hover:bg-slate-700 flex items-center space-x-1.5';
    
    AppState.markers.forEach(m => {
      if (AppState.map.hasLayer(m)) AppState.map.removeLayer(m);
    });
    updateHeatmapData();
    AppState.heatLayer.addTo(AppState.map);
  };

  // Toast Notification System
  function showToast(message, isError = false) {
    let toastContainer = document.getElementById('toast-container');
    if (!toastContainer) {
      toastContainer = document.createElement('div');
      toastContainer.id = 'toast-container';
      toastContainer.className = 'fixed top-5 right-5 z-50 flex flex-col space-y-2 pointer-events-none';
      document.body.appendChild(toastContainer);
    }

    const toast = document.createElement('div');
    toast.className = `px-4 py-2.5 rounded-xl shadow-2xl text-xs font-semibold flex items-center space-x-2 border transition-all duration-300 transform translate-y-[-10px] opacity-0 pointer-events-auto ${
      isError 
        ? 'bg-red-950/90 text-red-200 border-red-800' 
        : 'bg-slate-900/95 text-cyan-200 border-cyan-500/50'
    }`;
    toast.innerHTML = `
      <span class="w-2 h-2 rounded-full ${isError ? 'bg-red-400' : 'bg-cyan-400 animate-ping'}"></span>
      <span>${message}</span>
    `;

    toastContainer.appendChild(toast);
    requestAnimationFrame(() => {
      toast.classList.remove('translate-y-[-10px]', 'opacity-0');
    });

    setTimeout(() => {
      toast.classList.add('opacity-0', 'translate-y-[-10px]');
      setTimeout(() => toast.remove(), 300);
    }, 4500);
  }

  // Sinkronisasi Berita Viral Harian (Tombol Radar Berita Viral)
  const btnSyncViral = document.getElementById('btn-sync-viral');
  if (btnSyncViral) {
    btnSyncViral.onclick = async () => {
      const syncIcon = document.getElementById('btn-sync-icon');
      const syncText = document.getElementById('btn-sync-text');

      btnSyncViral.disabled = true;
      if (syncIcon) syncIcon.classList.add('animate-spin');
      if (syncText) syncText.innerText = 'Menyinkronkan...';

      try {
        const res = await fetch('/api/viral/sync', { method: 'POST' });
        const data = await res.json();
        if (data.success) {
          showToast(`Sinkronisasi selesai! Berita viral hari ini (${data.data?.fetched || 0} artikel) berhasil divalidasi.`);
          AppState.threats.clear();
          AppState.markers.forEach(m => AppState.map.removeLayer(m));
          AppState.markers.clear();
          updateHeatmapData();
          renderFeedList();
          await fetchInitialThreats();
          await fetchStats();
        } else {
          showToast(`Gagal sinkronisasi: ${data.error || 'Server error'}`, true);
        }
      } catch (err) {
        showToast('Koneksi sinkronisasi gagal.', true);
      } finally {
        btnSyncViral.disabled = false;
        if (syncIcon) syncIcon.classList.remove('animate-spin');
        if (syncText) syncText.innerText = 'Sinkronkan Hari Ini';
      }
    };
  }

  // MODAL CEK BERITA MANDIRI
  const modalChecker = document.getElementById('modal-checker');
  const btnOpenChecker = document.getElementById('btn-open-checker');
  const btnCloseChecker = document.getElementById('btn-close-checker');
  const btnRunAnalysis = document.getElementById('btn-run-analysis');
  const btnPlotToMap = document.getElementById('btn-plot-to-map');

  btnOpenChecker.onclick = () => {
    modalChecker.classList.remove('hidden');
    const searchVal = inputSearch.value.trim();
    if (searchVal) {
      document.getElementById('checker-input-text').value = searchVal;
    }
  };

  btnCloseChecker.onclick = () => {
    modalChecker.classList.add('hidden');
  };

  modalChecker.onclick = (e) => {
    if (e.target === modalChecker) {
      modalChecker.classList.add('hidden');
    }
  };

  if (btnRunAnalysis) {
    btnRunAnalysis.onclick = () => runNewsAnalysis(false);
  }
  if (btnPlotToMap) {
    btnPlotToMap.onclick = () => runNewsAnalysis(true);
  }

  const btnDirectPlot = document.getElementById('btn-direct-plot');
  if (btnDirectPlot) {
    btnDirectPlot.onclick = () => {
      runNewsAnalysis(true);
    };
  }
}

// Inisialisasi Utama
window.addEventListener('DOMContentLoaded', () => {
  initMap();
  initCharts();
  initWebSocket();
  initUIEvents();
  loadCitiesDatalist();
  fetchInitialThreats();
  fetchStats();
});
