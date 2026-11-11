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
  currentFilter: { label: 'all', sumber: 'all', search: '', provinsi: 'all' },
  provinces: [], // daftar 38 provinsi dari API
  activeLayer: 'markers', // 'markers' | 'heatmap'
  basemapMode: 'osm_light',
  map: null,
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

  // Layer: OpenStreetMap Standard Light (OSM Light Eksklusif & Tunggal)
  AppState.osmTile = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    maxZoom: 19
  });

  // Pasang OpenStreetMap Standard Light secara permanen
  AppState.osmTile.addTo(AppState.map);

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
  } else if (props.sumber === 'tiktok') {
    return `https://www.tiktok.com/search?q=${encodeURIComponent(cleanQuery)}`;
  } else if (props.sumber === 'instagram') {
    const firstTag = cleanQuery.split(/\s+/)[0] || 'berita';
    return `https://www.instagram.com/explore/tags/${encodeURIComponent(firstTag)}/`;
  } else if (props.sumber === 'twitter') {
    return `https://twitter.com/search?q=${encodeURIComponent(cleanQuery)}&f=live`;
  } else {
    return `https://news.google.com/search?q=${encodeURIComponent(cleanQuery)}&hl=id&gl=ID&ceid=ID:id`;
  }
}

// Helper: Dapatkan icon Lucide berdasarkan sumber
function getLinkIconBySource(sumber) {
  if (sumber === 'twitter') return 'message-circle';
  if (sumber === 'tiktok') return 'video';
  if (sumber === 'instagram') return 'camera';
  if (sumber === 'mastodon') return 'users';
  if (sumber === 'bluesky') return 'cloud';
  if (sumber === 'reddit') return 'message-square';
  if (sumber === 'turnbackhoax') return 'shield-alert';
  return 'newspaper';
}

// Helper: Kumpulkan SEMUA tautan rujukan untuk berita ini dan berita sejenis/serupa
function getAllReferenceLinks(props) {
  const links = [];
  const seenUrls = new Set();

  function add(url, title, icon, badge) {
    if (!url || seenUrls.has(url) || url.includes('geosentry.id')) return;
    seenUrls.add(url);
    links.push({ url, title, icon, badge });
  }

  // 1. Tautan rujukan eksplisit dari kolom referensi_urls
  if (props.referensi_urls) {
    let refs = props.referensi_urls;
    if (typeof refs === 'string') {
      try { refs = JSON.parse(refs); } catch(e) { refs = []; }
    }
    if (Array.isArray(refs)) {
      refs.forEach(r => {
        if (typeof r === 'string' && r.startsWith('http')) {
          add(r, 'Rujukan Terkait', 'external-link', 'RUJUKAN');
        } else if (r && r.url) {
          add(r.url, r.judul || 'Rujukan Sumber', getLinkIconBySource(r.sumber), (r.sumber || 'berita').toUpperCase());
        }
      });
    }
  }

  // 2. Tautan utama props.url
  if (props.url && (props.url.startsWith('http://') || props.url.startsWith('https://')) && !props.url.includes('geosentry.id')) {
    let badgeName = (props.sumber || 'BERITA').toUpperCase();
    let iconName = getLinkIconBySource(props.sumber);
    add(props.url, 'Laporan & Sumber Asli', iconName, badgeName);
  }

  // 3. Cari berita yang SAMA atau SERUPA dari database aktif di peta (Cross-Reference Grouping)
  if (props.teks && typeof AppState !== 'undefined' && AppState.threats) {
    const rawWords = props.teks.toLowerCase()
      .replace(/[^a-z0-9\s]/g, ' ')
      .split(/\s+/)
      .filter(w => w.length > 4 && !['viral', 'beredar', 'adalah', 'dengan', 'untuk', 'bahwa', 'terkait', 'dalam', 'kota', 'negeri', 'indonesia', 'pemerintah', 'wilayah'].includes(w));
    
    const keyTopics = Array.from(new Set(rawWords)).slice(0, 4);

    if (keyTopics.length > 0) {
      AppState.threats.forEach(otherFeature => {
        const other = otherFeature.properties;
        if (other && other.id !== props.id && other.url && !seenUrls.has(other.url)) {
          const otherText = (other.teks || '').toLowerCase();
          const matchCount = keyTopics.filter(k => otherText.includes(k)).length;
          if (matchCount >= 2 || (keyTopics.length === 1 && matchCount === 1)) {
            let labelBadge = (other.sumber || 'TERKAIT').toUpperCase();
            let titleText = `Laporan Terkait (${other.lokasi_nama || 'Wilayah Lain'})`;
            add(other.url, titleText, getLinkIconBySource(other.sumber), labelBadge);
          }
        }
      });
    }
  }

  // 4. Pastikan selalu ada tautan periksa fakta / Google News resmi
  const fallbackUrl = getRelatedNewsUrl(props);
  if (!seenUrls.has(fallbackUrl)) {
    if (props.label === 'hoaks') {
      add(fallbackUrl, 'Klarifikasi Fakta (TurnBackHoax)', 'shield-alert', 'CEK FAKTA');
    } else {
      add(fallbackUrl, 'Penelusuran Berita (Google News)', 'search', 'GOOGLE NEWS');
    }
  }

  return links;
}

// Helper: Format Popup Interaktif & Tombol Feedback Loop
function createPopupContent(props) {
  const labelColor = props.label === 'hoaks' ? 'text-red-400 bg-red-950/60 border-red-800' : 
                    (props.label === 'fakta' ? 'text-blue-400 bg-blue-950/60 border-blue-800' : 'text-amber-400 bg-amber-950/60 border-amber-800');
  
  const probPercent = Math.round((props.probabilitas || 0.5) * 100);
  const timeStr = new Date(props.dibuat_pada || Date.now()).toLocaleTimeString('id-ID');

  let sumberLabel = '📰 Berita';
  if (props.sumber === 'twitter') sumberLabel = '🐦 Twitter';
  else if (props.sumber === 'tiktok') sumberLabel = '📱 TikTok';
  else if (props.sumber === 'instagram') sumberLabel = '📸 Instagram';
  else if (props.sumber === 'mastodon') sumberLabel = '🐘 Mastodon';
  else if (props.sumber === 'bluesky') sumberLabel = '🦋 Bluesky';
  else if (props.sumber === 'reddit') sumberLabel = '👽 Reddit';
  else if (props.sumber === 'cek_mandiri') sumberLabel = '✨ Cek Mandiri';

  const allLinks = getAllReferenceLinks(props);

  return `
    <div class="text-xs space-y-2.5 min-w-[270px] max-w-[340px]">
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

      <!-- Semua Tautan Referensi Berita Terkait -->
      <div class="pt-2 border-t border-slate-700/60 space-y-1.5">
        <div class="flex items-center justify-between text-[10px] text-slate-400">
          <span class="font-bold uppercase tracking-wider text-cyan-400 flex items-center">
            <i data-lucide="link-2" class="w-3 h-3 mr-1"></i> Tautan Referensi (${allLinks.length}):
          </span>
          <span class="text-[9px] text-slate-400 font-mono">Semua Sumber</span>
        </div>
        <div class="space-y-1 max-h-32 overflow-y-auto pr-0.5">
          ${allLinks.map(l => `
            <a href="${l.url}" target="_blank" rel="noopener noreferrer" 
               class="flex items-center justify-between px-2 py-1.5 rounded-lg bg-cyan-950/70 hover:bg-cyan-900/90 border border-cyan-800/60 text-slate-200 hover:text-white transition group text-[11px] font-medium">
              <span class="flex items-center space-x-1.5 truncate mr-1.5">
                <i data-lucide="${l.icon}" class="w-3.5 h-3.5 text-cyan-400 group-hover:scale-110 transition shrink-0"></i>
                <span class="truncate max-w-[170px]">${l.title}</span>
              </span>
              <span class="text-[9px] font-mono uppercase bg-slate-900/90 border border-slate-700/70 px-1 py-0.5 rounded text-cyan-300 shrink-0">
                ${l.badge} &rarr;
              </span>
            </a>
          `).join('')}
        </div>
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
  const { label, sumber, search, provinsi } = AppState.currentFilter;
  const p = feature.properties;

  const matchLabel = label === 'all' || p.label === label;
  const matchSumber = sumber === 'all' || p.sumber === sumber;
  
  // Filter Provinsi
  let matchProv = true;
  if (provinsi && provinsi !== 'all') {
    const provLower = provinsi.toLowerCase().trim();
    const pProv = (p.provinsi || '').toLowerCase().trim();
    const pLokasi = (p.lokasi_nama || '').toLowerCase().trim();
    matchProv = pProv === provLower || pLokasi.includes(provLower);
  }

  let matchSearch = true;
  if (search && search.trim() !== '') {
    const q = search.toLowerCase().trim();
    const textMatch = (p.teks || '').toLowerCase().includes(q);
    const locMatch = (p.lokasi_nama || '').toLowerCase().includes(q);
    const labelMatch = (p.label || '').toLowerCase().includes(q);
    matchSearch = textMatch || locMatch || labelMatch;
  }

  return matchLabel && matchSumber && matchProv && matchSearch;
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
      datasets: [
        {
          label: 'Hoaks',
          data: [],
          backgroundColor: '#ef4444',
          borderRadius: 3
        },
        {
          label: 'Fakta',
          data: [],
          backgroundColor: '#3b82f6',
          borderRadius: 3
        },
        {
          label: 'Negatif',
          data: [],
          backgroundColor: '#f59e0b',
          borderRadius: 3
        }
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      indexAxis: 'y',
      scales: {
        x: { 
          stacked: true,
          grid: { color: 'rgba(51, 65, 85, 0.4)' }, 
          ticks: { color: '#94a3b8', font: { size: 9 }, stepSize: 2 } 
        },
        y: { 
          stacked: true,
          grid: { display: false }, 
          ticks: { color: '#e2e8f0', font: { size: 10, weight: 'bold' } } 
        }
      },
      plugins: {
        legend: { display: false },
        tooltip: {
          mode: 'index',
          intersect: false,
          callbacks: {
            footer: (tooltipItems) => {
              let sum = 0;
              tooltipItems.forEach(item => {
                sum += item.parsed.x;
              });
              return `Total Kasus: ${sum}`;
            }
          }
        }
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

  // Update grafik stacked bar Wilayah Terpanas dengan data faktual per kategori
  if (AppState.chartRegions && stats.top_regions) {
    AppState.chartRegions.data.labels = stats.top_regions.map(r => r.lokasi_nama);
    AppState.chartRegions.data.datasets[0].data = stats.top_regions.map(r => parseInt(r.hoaks || 0, 10));
    AppState.chartRegions.data.datasets[1].data = stats.top_regions.map(r => parseInt(r.fakta || 0, 10));
    AppState.chartRegions.data.datasets[2].data = stats.top_regions.map(r => parseInt(r.negatif || 0, 10));
    AppState.chartRegions.update();
  }

  // Tampilkan fakta wilayah terbanyak per kategori (Hoaks, Fakta, Sentimen Negatif)
  if (stats.top_category_leaders) {
    const elH = document.getElementById('top-fact-hoaks');
    const elF = document.getElementById('top-fact-fakta');
    const elN = document.getElementById('top-fact-negatif');
    if (elH && stats.top_category_leaders.hoaks) {
      elH.innerText = `${stats.top_category_leaders.hoaks.lokasi_nama} (${stats.top_category_leaders.hoaks.count})`;
    }
    if (elF && stats.top_category_leaders.fakta) {
      elF.innerText = `${stats.top_category_leaders.fakta.lokasi_nama} (${stats.top_category_leaders.fakta.count})`;
    }
    if (elN && stats.top_category_leaders.negatif) {
      elN.innerText = `${stats.top_category_leaders.negatif.lokasi_nama} (${stats.top_category_leaders.negatif.count})`;
    }
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
  else if (props.sumber === 'tiktok') sumberIcon = '📱 TikTok';
  else if (props.sumber === 'instagram') sumberIcon = '📸 Instagram';
  else if (props.sumber === 'mastodon') sumberIcon = '🐘 Mastodon';
  else if (props.sumber === 'bluesky') sumberIcon = '🦋 Bluesky';
  else if (props.sumber === 'reddit') sumberIcon = '👽 Reddit';
  else if (props.sumber === 'cek_mandiri') sumberIcon = '✨ Cek Mandiri';

  const allRefs = getAllReferenceLinks(props);
  const primaryLink = allRefs[0] ? allRefs[0].url : getRelatedNewsUrl(props);
  const refCountText = allRefs.length > 1 ? `🔗 ${allRefs.length} Rujukan` : (props.sumber || 'BERITA').toUpperCase();

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
      <a href="${primaryLink}" target="_blank" rel="noopener noreferrer" 
         onclick="event.stopPropagation();" 
         class="text-cyan-400 hover:text-cyan-200 transition flex items-center space-x-1 hover:underline font-semibold bg-cyan-950/70 px-1.5 py-0.5 rounded border border-cyan-800/50">
        <span>${refCountText}</span>
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
    loadProvinces();
    showToast(`Berita viral harian disinkronkan (+${data.result?.inserted || 0} baru).`);
  });

  // Event saat sinkronisasi media sosial gratis (Mastodon / Bluesky / Reddit) selesai
  AppState.socket.on('social_synced', (data) => {
    console.log('[+] Media sosial baru tersinkronkan:', data);
    fetchInitialThreats();
    fetchStats();
    loadProvinces();
    showToast(`Media sosial disinkronkan (+${data.result?.inserted || 0} postingan baru).`);
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
    loadProvinces();
    showToast(data.message || 'Pukul 00:00 WIB: Jendela pemantauan bergeser ke hari ini dan kemarin.');
  });

  // Event saat data kadaluarsa (>2 hari) dibersihkan dari server
  AppState.socket.on('threats_purged', (data) => {
    console.log('[🧹] Data kadaluarsa >2 hari otomatis disingkirkan dari peta:', data);
    fetchInitialThreats();
    fetchStats();
    loadProvinces();
    showToast(`Pembersihan otomatis: ${data.count || 0} berita yang lewat >2 hari dihilangkan dari peta.`);
  });

  AppState.socket.on('viral_engine_ready', (info) => {
    console.log('[✓]', info.message, info.rollover_schedule);
  });
}

// Pemeriksa kadaluarsa klien: memastikan bila ada berita > 2 hari langsung hilang dari peta
function purgeExpiredThreatsClient() {
  const cutoff = Date.now() - (48 * 60 * 60 * 1000); // 48 jam / 2 hari
  let purgedCount = 0;
  AppState.threats.forEach((feature, id) => {
    const time = new Date(feature.properties.dibuat_pada).getTime();
    if (time < cutoff) {
      const marker = AppState.markers.get(id);
      if (marker) {
        AppState.map.removeLayer(marker);
        AppState.markers.delete(id);
      }
      AppState.threats.delete(id);
      const feedItem = document.getElementById(`feed-item-${id}`);
      if (feedItem) feedItem.remove();
      purgedCount++;
    }
  });
  if (purgedCount > 0) {
    console.log(`[🧹 Purge Klien] ${purgedCount} titik berita >2 hari otomatis dihilangkan dari peta.`);
    updateHeatmapData();
    renderFeedList();
    fetchStats();
  }
}
setInterval(purgeExpiredThreatsClient, 60000); // Evaluasi tiap 1 menit

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

// Memuat Direktori 38 Provinsi beserta statistik berita viral 48 jam terakhir
async function loadProvinces() {
  try {
    const res = await fetch('/api/provinces');
    const data = await res.json();
    if (Array.isArray(data)) {
      AppState.provinces = data;
      
      // Update Dropdown Filter Provinsi
      const selectProv = document.getElementById('filter-provinsi');
      if (selectProv) {
        const curVal = AppState.currentFilter.provinsi || 'all';
        selectProv.innerHTML = '<option value="all">🇮🇩 Seluruh Indonesia (38 Provinsi)</option>';
        data.forEach(p => {
          const opt = document.createElement('option');
          opt.value = p.provinsi;
          opt.innerText = `${p.provinsi} (${p.total} berita)`;
          selectProv.appendChild(opt);
        });
        selectProv.value = curVal;
      }

      // Update Modal 38 Provinsi
      renderProvinceCards(data);
      const totalCountBadge = document.getElementById('modal-prov-total-count');
      if (totalCountBadge) {
        totalCountBadge.innerText = data.length;
      }
    }
  } catch (err) {
    console.error('[!] Gagal memuat data provinsi:', err);
  }
}

// Render kartu provinsi di dalam Modal Jelajah 38 Provinsi
function renderProvinceCards(provincesList) {
  const container = document.getElementById('province-cards-container');
  if (!container) return;

  container.innerHTML = '';
  if (!provincesList || provincesList.length === 0) {
    container.innerHTML = `
      <div class="col-span-full py-12 text-center text-slate-400 text-xs">
        <i data-lucide="map-pin-off" class="w-8 h-8 mx-auto mb-2 opacity-50"></i>
        Tidak ada data provinsi yang cocok dengan pencarian.
      </div>
    `;
    if (window.lucide) lucide.createIcons();
    return;
  }

  provincesList.forEach(prov => {
    const card = document.createElement('div');
    const isActive = (AppState.currentFilter.provinsi || '').toLowerCase() === (prov.provinsi || '').toLowerCase();
    
    card.className = `p-3 rounded-xl border transition cursor-pointer flex flex-col justify-between group ${
      isActive 
        ? 'bg-cyan-950/80 border-cyan-400 shadow-lg shadow-cyan-900/40 ring-1 ring-cyan-400' 
        : 'bg-slate-900/90 border-slate-800 hover:border-cyan-500/60 hover:bg-slate-800'
    }`;

    card.innerHTML = `
      <div class="flex items-start justify-between mb-2">
        <div class="flex items-center space-x-2">
          <div class="w-7 h-7 rounded-lg bg-slate-800 group-hover:bg-cyan-900/60 border border-slate-700 group-hover:border-cyan-500/50 flex items-center justify-center shrink-0 transition">
            <i data-lucide="map-pin" class="w-3.5 h-3.5 ${isActive ? 'text-cyan-400' : 'text-slate-400 group-hover:text-cyan-300'}"></i>
          </div>
          <div>
            <h4 class="text-xs font-bold text-slate-100 group-hover:text-cyan-200 transition">${prov.provinsi}</h4>
            <span class="text-[10px] text-slate-400 font-mono">Pusat: [${prov.center_lat || '-'}, ${prov.center_lon || '-'}]</span>
          </div>
        </div>
        <span class="px-2 py-0.5 rounded-full text-[10px] font-mono font-bold ${
          prov.total > 0 ? 'bg-cyan-950 text-cyan-300 border border-cyan-800/80' : 'bg-slate-800 text-slate-400'
        }">
          ${prov.total} Berita
        </span>
      </div>

      <!-- Kategori Breakdown Badges -->
      <div class="flex items-center space-x-1.5 text-[10px] pt-2 border-t border-slate-800/80">
        <span class="px-1.5 py-0.5 rounded bg-red-950/60 border border-red-900/50 text-red-400 font-mono" title="Hoaks">
          🔴 ${prov.hoaks || 0}
        </span>
        <span class="px-1.5 py-0.5 rounded bg-blue-950/60 border border-blue-900/50 text-blue-400 font-mono" title="Fakta">
          🔵 ${prov.fakta || 0}
        </span>
        <span class="px-1.5 py-0.5 rounded bg-amber-950/60 border border-amber-900/50 text-amber-400 font-mono" title="Sentimen Negatif">
          🟡 ${prov.negatif || 0}
        </span>
        <div class="flex-1 text-right">
          <span class="text-[10px] text-cyan-400 group-hover:text-cyan-200 font-semibold flex items-center justify-end space-x-0.5">
            <span>Pilih</span>
            <i data-lucide="chevron-right" class="w-3 h-3"></i>
          </span>
        </div>
      </div>
    `;

    card.onclick = () => {
      selectProvince(prov);
      const modal = document.getElementById('modal-provinces');
      if (modal) modal.classList.add('hidden');
    };

    container.appendChild(card);
  });

  if (window.lucide && typeof lucide.createIcons === 'function') {
    lucide.createIcons();
  }
}

// Memilih provinsi dan memusatkan peta
function selectProvince(prov) {
  if (!prov || prov.provinsi === 'all') {
    AppState.currentFilter.provinsi = 'all';
    const select = document.getElementById('filter-provinsi');
    if (select) select.value = 'all';
    AppState.map.flyTo([-2.2, 118.0], 5, { duration: 1.2 });
    applyFilters();
    showToast('Menampilkan seluruh wilayah Indonesia (38 Provinsi)');
    return;
  }

  AppState.currentFilter.provinsi = prov.provinsi;
  const select = document.getElementById('filter-provinsi');
  if (select) select.value = prov.provinsi;

  if (prov.center_lat && prov.center_lon) {
    AppState.map.flyTo([prov.center_lat, prov.center_lon], 7, { duration: 1.4 });
  }

  applyFilters();
  showToast(`Memantau Provinsi ${prov.provinsi}: ${prov.total} berita viral aktif.`);
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

  // Filter Provinsi
  const selectProv = document.getElementById('filter-provinsi');
  if (selectProv) {
    selectProv.onchange = (e) => {
      const val = e.target.value;
      if (val === 'all') {
        selectProvince({ provinsi: 'all' });
      } else {
        const found = AppState.provinces.find(p => p.provinsi.toLowerCase() === val.toLowerCase());
        if (found) {
          selectProvince(found);
        } else {
          AppState.currentFilter.provinsi = val;
          applyFilters();
        }
      }
    };
  }

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

  // Tombol Sinkronisasi Media Sosial Gratis (Mastodon / Bluesky / Reddit)
  const btnSyncSocial = document.getElementById('btn-sync-social');
  if (btnSyncSocial) {
    btnSyncSocial.onclick = async () => {
      const labelSpan = btnSyncSocial.querySelector('span');
      const originalText = labelSpan ? labelSpan.innerText : 'Sinkronisasi Media Sosial';
      btnSyncSocial.disabled = true;
      if (labelSpan) labelSpan.innerText = 'Menarik data...';
      try {
        const res = await fetch('/api/social/sync', { method: 'POST' });
        const data = await res.json();
        if (data.success) {
          showToast(`Media sosial: +${data.data?.inserted || 0} baru, ${data.data?.updated || 0} diperbarui.`);
        } else {
          showToast('Sinkronisasi media sosial gagal: ' + (data.error || 'tidak diketahui'), true);
        }
      } catch (err) {
        showToast('Gagal terhubung ke server saat sinkronisasi media sosial.', true);
      } finally {
        btnSyncSocial.disabled = false;
        if (labelSpan) labelSpan.innerText = originalText;
      }
    };
  }


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

  // Pemantauan Berita Viral berjalan secara realtime otomatis di backend & WebSocket


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

  // MODAL DIREKTORI 38 PROVINSI
  const modalProvinces = document.getElementById('modal-provinces');
  const btnOpenProvinces = document.getElementById('btn-open-province-modal');
  const btnOpenProvincesSide = document.getElementById('btn-open-province-modal-side');
  const btnCloseProvinces = document.getElementById('btn-close-provinces');
  const btnCloseProvincesFooter = document.getElementById('btn-close-provinces-footer');
  const inputFilterModal = document.getElementById('input-filter-province-modal');
  const btnResetProvFilter = document.getElementById('btn-reset-province-filter');

  const openProvModal = () => {
    if (modalProvinces) {
      modalProvinces.classList.remove('hidden');
      if (inputFilterModal) inputFilterModal.value = '';
      renderProvinceCards(AppState.provinces);
    }
  };

  const closeProvModal = () => {
    if (modalProvinces) modalProvinces.classList.add('hidden');
  };

  if (btnOpenProvinces) btnOpenProvinces.onclick = openProvModal;
  if (btnOpenProvincesSide) btnOpenProvincesSide.onclick = openProvModal;
  if (btnCloseProvinces) btnCloseProvinces.onclick = closeProvModal;
  if (btnCloseProvincesFooter) btnCloseProvincesFooter.onclick = closeProvModal;

  if (modalProvinces) {
    modalProvinces.onclick = (e) => {
      if (e.target === modalProvinces) closeProvModal();
    };
  }

  if (inputFilterModal) {
    inputFilterModal.addEventListener('input', (e) => {
      const q = e.target.value.toLowerCase().trim();
      if (!q) {
        renderProvinceCards(AppState.provinces);
      } else {
        const filtered = AppState.provinces.filter(p => p.provinsi.toLowerCase().includes(q));
        renderProvinceCards(filtered);
      }
    });
  }

  if (btnResetProvFilter) {
    btnResetProvFilter.onclick = () => {
      selectProvince({ provinsi: 'all' });
      closeProvModal();
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
  loadProvinces();
});
