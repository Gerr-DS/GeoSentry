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
  currentFilter: { label: 'all', search: '', provinsi: 'all' },
  provinces: [], // daftar 38 provinsi dari API
  markersVisible: true,
  basemapMode: 'osm_light',
  map: null,
  osmTile: null,
  chartCategory: null,
  socket: null,
  lastAnalysis: null
};

function getClassificationDisplay(label) {
  if (label === 'hoaks') return { text: 'Hoaks', color: 'red' };
  if (label === 'fakta') return { text: 'Fakta', color: 'blue' };
  return { text: 'Belum dapat dipastikan', color: 'slate' };
}

function getSourceName(source) {
  const names = {
    berita: 'Portal berita',
    turnbackhoax: 'TurnBackHoax',
    twitter: 'X / Twitter',
    x: 'X',
    tiktok: 'TikTok',
    instagram: 'Instagram',
    mastodon: 'Mastodon',
    bluesky: 'Bluesky',
    reddit: 'Reddit',
    cek_mandiri: 'Analisis manual'
  };
  return names[source] || String(source || 'Portal berita');
}

// Analisis dan event Socket.IO di luar DOMContentLoaded juga memakai toast.
window.showToast = function showToast(message, isError = false) {
  let container = document.getElementById('toast-container');
  if (!container) {
    container = document.createElement('div');
    container.id = 'toast-container';
    container.className = 'fixed top-5 right-5 z-50 flex flex-col space-y-2 pointer-events-none';
    document.body.appendChild(container);
  }

  const toast = document.createElement('div');
  toast.className = `px-4 py-2.5 rounded-lg shadow-lg text-xs font-semibold flex items-center space-x-2 border transition-all duration-300 transform translate-y-[-6px] opacity-0 pointer-events-auto ${
    isError ? 'bg-red-50 text-red-800 border-red-200' : 'app-surface text-slate-800 border-slate-300'
  }`;
  const indicator = document.createElement('span');
  indicator.className = `w-2 h-2 rounded-full ${isError ? 'bg-red-400' : 'bg-slate-700'}`;
  const text = document.createElement('span');
  text.textContent = message;
  toast.append(indicator, text);
  container.appendChild(toast);
  requestAnimationFrame(() => toast.classList.remove('translate-y-[-10px]', 'opacity-0'));

  setTimeout(() => {
    toast.classList.add('opacity-0', 'translate-y-[-10px]');
    setTimeout(() => toast.remove(), 300);
  }, 4500);
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
  const markerClass = label === 'fakta' ? 'marker-fakta' : 'marker-hoaks';
  return L.divIcon({
    className: 'custom-leaflet-pin',
    html: `<div class="marker-pin ${markerClass}"></div>`,
    iconSize: [20, 20],
    iconAnchor: [10, 10],
    popupAnchor: [0, -10]
  });
}

// A URL pencarian bukan sumber berita yang sudah diverifikasi.
function isSearchResultUrl(url) {
  return /(?:google\.[^/]+\/search|news\.google\.com\/search|turnbackhoax\.id\/?\?s=|twitter\.com\/search|x\.com\/search|tiktok\.com\/search|instagram\.com\/explore\/tags)/i.test(url || '');
}

// Helper: tautkan sumber asli jika tersedia, selain itu arahkan ke pencarian teks.
function getRelatedNewsUrl(props) {
  if (props.url && (props.url.startsWith('http://') || props.url.startsWith('https://')) && !props.url.includes('geosentry.id')) {
    return props.url;
  }
  let cleanQuery = (props.teks || '')
    .replace(/^\[.*?\]\s*/, '')
    .replace(/^(Viral|Awas|Waspada|Kabar heboh|Informasi A1|Bongkar|Klarifikasi)[:!]\s*/i, '')
    .replace(/["'”]/g, ' ')
    .replace(/\s+/g, ' ')
    .slice(0, 220)
    .trim();

  if (!cleanQuery) cleanQuery = props.lokasi_nama || 'berita Indonesia';
  return `https://www.google.com/search?q=${encodeURIComponent(cleanQuery)}`;
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
          const isSearch = isSearchResultUrl(r);
          add(r, isSearch ? 'Hasil pencarian, belum diverifikasi' : 'Sumber rujukan', isSearch ? 'search' : 'external-link', isSearch ? 'Pencarian' : 'Rujukan');
        } else if (r && r.url) {
          const isSearch = isSearchResultUrl(r.url);
          add(r.url, isSearch ? 'Hasil pencarian, belum diverifikasi' : (r.judul || 'Sumber rujukan'), isSearch ? 'search' : getLinkIconBySource(r.sumber), isSearch ? 'Pencarian' : getSourceName(r.sumber));
        }
      });
    }
  }

  // 2. Tautan utama props.url
  if (props.url && (props.url.startsWith('http://') || props.url.startsWith('https://')) && !props.url.includes('geosentry.id')) {
    const isSearch = isSearchResultUrl(props.url);
    let badgeName = isSearch ? 'Pencarian' : getSourceName(props.sumber);
    let iconName = isSearch ? 'search' : getLinkIconBySource(props.sumber);
    add(props.url, isSearch ? 'Hasil pencarian, belum diverifikasi' : 'Sumber asli', iconName, badgeName);
  }

  // 3. Jika tidak ada sumber asli, sediakan pencarian teks yang jelas statusnya.
  const fallbackUrl = getRelatedNewsUrl(props);
  if (!seenUrls.has(fallbackUrl)) {
    add(fallbackUrl, 'Hasil pencarian, belum diverifikasi', 'search', 'Pencarian');
  }

  return links;
}

// Helper: Format Popup Interaktif & Tombol Feedback Loop
function createPopupContent(props) {
  const isHumanReviewed = Boolean(props.dikoreksi);
  const labelColor = props.label === 'hoaks' ? 'text-red-700 bg-red-50 border-red-200' :
    (props.label === 'fakta' ? 'text-blue-700 bg-blue-50 border-blue-200' :
      'text-slate-700 bg-slate-100 border-slate-300');
  
  const probPercent = Math.round((props.probabilitas ?? 0) * 100);
  const timeStr = new Date(props.dibuat_pada || Date.now()).toLocaleTimeString('id-ID');

  const sumberLabel = getSourceName(props.sumber);
  const classification = getClassificationDisplay(props.label);

  const allLinks = getAllReferenceLinks(props);

  return `
    <div class="text-xs space-y-2.5 min-w-[270px] max-w-[340px]">
      <div class="flex items-center justify-between border-b border-slate-300 pb-1.5">
        <span class="px-2 py-0.5 rounded text-xs font-semibold border ${labelColor}">
          ${classification.text}
        </span>
        <span class="text-xs text-slate-500">
          ${sumberLabel} &bull; ${timeStr}
        </span>
      </div>

      <p class="text-slate-800 text-xs font-normal leading-relaxed line-clamp-3">
        "${props.teks}"
      </p>

      ${props.verifikasi_status === 'prediksi_sementara' ? `
      <div class="text-xs leading-relaxed text-slate-700 bg-slate-100 border border-slate-300 rounded px-2 py-1.5">
        Prediksi dari AI; bisa saja keliru dan belum terverifikasi. ${props.verifikasi_dasar || ''}
      </div>` : props.verifikasi_status === 'terverifikasi' && props.verifikasi_dasar ? `
      <div class="text-xs leading-relaxed text-slate-700 bg-slate-100 border border-slate-300 rounded px-2 py-1.5">
        Diperiksa: ${props.verifikasi_dasar}
      </div>` : ''}

      ${isHumanReviewed ? `
      <div class="text-xs leading-relaxed text-slate-700 bg-slate-100 border border-slate-300 rounded px-2 py-1.5">
        Label ini sudah dikoreksi oleh analis.
      </div>` : `<div class="space-y-1">
        <div class="flex justify-between text-xs text-slate-500">
          <span>Perkiraan keyakinan:</span>
          <span class="font-semibold text-slate-800">${probPercent}%</span>
        </div>
        <div class="w-full bg-slate-100 rounded-full h-1.5 overflow-hidden">
          <div class="h-1.5 rounded-full ${props.label === 'hoaks' ? 'bg-red-500' : (props.label === 'fakta' ? 'bg-blue-500' : 'bg-slate-500') }" style="width: ${probPercent}%"></div>
        </div>
      </div>`}

      <div class="flex items-center text-xs text-slate-500">
        Lokasi: <strong class="text-slate-800 ml-1">${props.lokasi_nama || 'Nasional'}</strong>
      </div>

      <!-- Semua Tautan Referensi Berita Terkait -->
      <div class="pt-2 border-t border-slate-300 space-y-1.5">
        <div class="flex items-center justify-between text-xs text-slate-500">
          <span class="font-semibold text-slate-800 flex items-center">
            <i data-lucide="link-2" class="w-3 h-3 mr-1"></i> Sumber terkait (${allLinks.length}):
          </span>
          <span class="text-xs text-slate-500">Tautan</span>
        </div>
        <div class="space-y-1 max-h-32 overflow-y-auto pr-0.5">
          ${allLinks.map(l => `
            <a href="${l.url}" target="_blank" rel="noopener noreferrer" 
               class="flex items-center justify-between px-2 py-1.5 rounded-lg bg-slate-100 hover:bg-slate-200 border border-slate-300 text-slate-800 hover:text-slate-900 transition group text-xs font-medium">
              <span class="flex items-center space-x-1.5 truncate mr-1.5">
                <i data-lucide="${l.icon}" class="w-3.5 h-3.5 text-slate-700 group-hover:scale-110 transition shrink-0"></i>
                <span class="truncate max-w-[170px]">${l.title}</span>
              </span>
              <span class="text-xs app-surface border border-slate-300 px-1 py-0.5 rounded text-slate-700 shrink-0">
                ${l.badge} &rarr;
              </span>
            </a>
          `).join('')}
        </div>
      </div>

      <!-- Feedback Loop Koreksi Analis -->
      <div class="pt-1.5 border-t border-slate-300 flex items-center justify-between gap-1.5">
        <span class="text-xs text-slate-600 font-semibold flex items-center">
          Koreksi label:
        </span>
        <div class="flex items-center space-x-1">
          <button onclick="submitCorrection(${props.id}, 'fakta')" class="px-2 py-1 rounded bg-blue-700 hover:bg-blue-800 text-slate-100 text-xs font-bold transition">
            Fakta
          </button>
          <button onclick="submitCorrection(${props.id}, 'hoaks')" class="px-2 py-1 rounded bg-red-700 hover:bg-red-800 text-slate-100 text-xs font-bold transition">
            Hoaks
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
  if (!['hoaks', 'fakta'].includes(feature?.properties?.label)) return;
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

  if (isFeatureMatchFilter(feature) && AppState.markersVisible) {
    marker.addTo(AppState.map);
  }

  if (autoFly && AppState.markersVisible) {
    AppState.map.flyTo([lat, lon], Math.max(AppState.map.getZoom(), 7), {
      duration: 1.2
    });
    setTimeout(() => marker.openPopup(), 1200);
  }

  prependThreatToFeed(feature.properties, true);
}

function isFeatureMatchFilter(feature) {
  const { label, search, provinsi } = AppState.currentFilter;
  const p = feature.properties;

  const matchLabel = label === 'all' || p.label === label;
  const supportedLabel = ['hoaks', 'fakta'].includes(p.label);
  
  // Filter Provinsi
  let matchProv = true;
  if (provinsi && provinsi !== 'all') {
    const provLower = provinsi.toLowerCase().trim();
    const pProv = (p.provinsi || '').toLowerCase().trim();
    const pLokasi = (p.lokasi_nama || '').toLowerCase().trim();

    const isNasionalThreat = pProv === 'nasional' || pLokasi.startsWith('nasional');

    if (provLower === 'nasional') {
      // Filter Nasional: hanya tampilkan berita nasional
      matchProv = isNasionalThreat;
    } else if (provLower === 'dki jakarta' || provLower === 'jakarta') {
      // Filter DKI Jakarta: exclude berita nasional
      matchProv = !isNasionalThreat && (
        pProv === 'dki jakarta' ||
        pLokasi.includes('jakarta') ||
        pLokasi.includes('kepulauan seribu')
      );
    } else {
      // Filter provinsi lain: exclude berita nasional
      matchProv = !isNasionalThreat && (
        pProv === provLower ||
        pLokasi.includes(provLower) ||
        (pProv && provLower.includes(pProv) && pProv !== 'nasional')
      );
    }
  }

  let matchSearch = true;
  if (search && search.trim() !== '') {
    const q = search.toLowerCase().trim();
    const textMatch = (p.teks || '').toLowerCase().includes(q);
    const locMatch = (p.lokasi_nama || '').toLowerCase().includes(q);
    const labelMatch = (p.label || '').toLowerCase().includes(q);
    matchSearch = textMatch || locMatch || labelMatch;
  }

  return supportedLabel && matchLabel && matchProv && matchSearch;
}

function applyFilters() {
  AppState.markers.forEach((marker, id) => {
    const feature = AppState.threats.get(id);
    if (feature && isFeatureMatchFilter(feature) && AppState.markersVisible) {
      if (!AppState.map.hasLayer(marker)) marker.addTo(AppState.map);
    } else {
      if (AppState.map.hasLayer(marker)) AppState.map.removeLayer(marker);
    }
  });

  renderFeedList();
}

// ==========================================
// 3. CHARTS & ANALITIK
// ==========================================

function initCharts() {
  const ctxCat = document.getElementById('chart-category').getContext('2d');
  AppState.chartCategory = new Chart(ctxCat, {
    type: 'doughnut',
    data: {
      labels: ['Hoaks', 'Fakta'],
      datasets: [{
        data: [0, 0],
        backgroundColor: ['#ef4444', '#3b82f6'],
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
          labels: { color: document.documentElement.dataset.theme === 'dark' ? '#cbd5e1' : '#475569', font: { size: 11, family: 'Inter, system-ui, sans-serif' }, boxWidth: 10 }
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

  const viralActiveEl = document.getElementById('viral-active-count');
  if (viralActiveEl) viralActiveEl.innerText = totalActive;

  const rangeEl = document.getElementById('window-range-text');
  if (rangeEl && (stats.hari_ini_count !== undefined || stats.kemarin_count !== undefined)) {
    rangeEl.innerText = `Hari Ini (${stats.hari_ini_count || 0}) & Kemarin (${stats.kemarin_count || 0})`;
  }

  if (AppState.chartCategory) {
    AppState.chartCategory.data.datasets[0].data = [
      stats.hoaks || 0,
      stats.fakta || 0
    ];
    AppState.chartCategory.update();
  }

}

// 4. FEED RADAR TICKER
// ==========================================

function prependThreatToFeed(props, isNew = false) {
  if (!['hoaks', 'fakta'].includes(props?.label)) return;
  const container = document.getElementById('feed-container');
  const emptyMsg = document.getElementById('empty-feed-msg');
  if (emptyMsg) emptyMsg.remove();

  const labelColor = props.label === 'hoaks' ? 'border-l-red-500 text-red-800' : 'border-l-blue-500 text-blue-800';
  
  const div = document.createElement('div');
  div.id = `feed-item-${props.id}`;
  div.className = `p-2.5 rounded-lg app-surface border border-slate-200 border-l-4 ${labelColor} text-xs transition cursor-pointer hover:bg-slate-100 ${isNew ? 'threat-item-new' : ''}`;
  
  div.onclick = () => {
    const marker = AppState.markers.get(props.id);
    if (marker) {
      AppState.map.flyTo(marker.getLatLng(), 8, { duration: 1 });
      setTimeout(() => marker.openPopup(), 1000);
    }
  };

  const sourceName = getSourceName(props.sumber);
  const labelName = getClassificationDisplay(props.label).text;

  const allRefs = getAllReferenceLinks(props);
  const primaryLink = allRefs[0] ? allRefs[0].url : getRelatedNewsUrl(props);
  const refCountText = allRefs.length > 1 ? `${allRefs.length} tautan` : (allRefs[0]?.badge || sourceName);

  div.innerHTML = `
    <div class="flex items-center justify-between mb-1">
      <span class="font-semibold text-xs">${labelName}${props.verifikasi_status === 'prediksi_sementara' ? ' · Sementara' : ''}</span>
      <span class="text-xs text-slate-500">${props.lokasi_nama || 'Nasional'}</span>
    </div>
    <p class="text-slate-700 line-clamp-2 leading-relaxed">
      ${props.teks}
    </p>
    <div class="mt-1.5 flex items-center justify-between text-xs text-slate-500">
      <span class="flex items-center space-x-1">${sourceName}</span>
      <a href="${primaryLink}" target="_blank" rel="noopener noreferrer" 
         onclick="event.stopPropagation();" 
         class="text-slate-700 hover:text-slate-900 transition flex items-center space-x-1 hover:underline font-semibold bg-slate-100 px-1.5 py-0.5 rounded border border-slate-300">
        <span>${refCountText}</span>
        <i data-lucide="external-link" class="w-2.5 h-2.5 ml-0.5"></i>
      </a>
      <span class="text-slate-500">Perkiraan ${Math.round((props.probabilitas || 0.5) * 100)}%</span>
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
        <i data-lucide="newspaper" class="w-8 h-8 mx-auto mb-2 opacity-40"></i>
        Tidak ada berita yang cocok dengan pilihan ini.
      </div>
    `;
    lucide.createIcons();
    return;
  }

  const maxRender = (AppState.currentFilter.provinsi && AppState.currentFilter.provinsi !== 'all') ? 200 : 50;
  filtered.slice(0, maxRender).forEach(feat => {
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
    alert('Silakan masukkan teks narasi atau link media sosial terlebih dahulu!');
    return;
  }

  const btnPlotText = document.getElementById('btn-plot-text');

  // Deteksi jika input adalah URL Media Sosial (TikTok, Instagram, YouTube, X, dll)
  const isUrl = /^https?:\/\//i.test(text);
  if (isUrl) {
    if (btnPlotText) btnPlotText.innerText = 'Membaca postingan dan mencari sumber...';
    try {
      const res = await fetch('/api/social/inspect-url', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: text, save_to_map: saveToMap })
      });
      const respData = await res.json();
      if (btnPlotText) btnPlotText.innerText = 'Analisis teks & cari sumber terkait';

      if (respData.success && respData.data) {
        const item = respData.data;
        const resultBox = document.getElementById('checker-result-box');
        if (resultBox) resultBox.classList.remove('hidden');

        // Update Badge Klasifikasi
        const badge = document.getElementById('res-badge');
        if (badge) {
          const display = getClassificationDisplay(item.label);
          if (display.color === 'red') {
            badge.className = 'px-2.5 py-0.5 rounded text-xs font-semibold bg-red-50 text-red-700 border border-red-200';
          } else if (display.color === 'blue') {
            badge.className = 'px-2.5 py-0.5 rounded text-xs font-semibold bg-blue-50 text-blue-700 border border-blue-200';
          } else {
            badge.className = 'px-2.5 py-0.5 rounded text-xs font-semibold bg-slate-100 text-slate-700 border border-slate-300';
          }
          badge.innerText = `${display.text} · ${getSourceName(item.sumber)}`;
        }

        // Update Confidence Bar
        const confPercent = Math.round(item.probabilitas * 100);
        const confEl = document.getElementById('res-confidence');
        if (confEl) confEl.innerText = `${confPercent}%`;
        const bar = document.getElementById('res-prob-bar');
        if (bar) {
          bar.style.width = `${confPercent}%`;
          bar.className = `h-2 rounded-full transition-all duration-500 ${item.label === 'hoaks' ? 'bg-red-500' : (item.label === 'fakta' ? 'bg-blue-500' : 'bg-slate-500')}`;
        }

        // Update Origin
        const originEl = document.getElementById('res-origin');
        if (originEl) originEl.innerText = `${item.lokasi_nama || 'Nasional'} (Prov: ${item.provinsi || 'Nasional'})`;

        // Update Media Info
        const spreadersList = document.getElementById('res-spreaders-list');
        if (spreadersList) {
          spreadersList.innerHTML = `
            <span class="px-2 py-0.5 rounded bg-slate-100 text-slate-700 border border-slate-300 text-xs">
              Sumber: ${getSourceName(item.sumber)}
            </span>
            <span class="px-2 py-0.5 rounded bg-slate-100 text-slate-700 border border-slate-300 text-xs truncate max-w-xs block mt-1">
              "${item.title || item.teks.slice(0, 50)}"
            </span>
          `;
        }

        // Link Rujukan Berita Terkait
        const newsLinkEl = document.getElementById('res-news-link');
        const newsLinkLabel = document.getElementById('res-news-link-label');
        if (newsLinkEl && item.url) {
          newsLinkEl.href = item.url;
          if (newsLinkLabel) newsLinkLabel.innerText = `Buka postingan ${getSourceName(item.sumber)} ↗`;
        }

        // Animasi peta flyTo
        if (item.lat && item.lon) {
          AppState.map.flyTo([item.lat, item.lon], 8, { duration: 1.5 });
        }

        showToast(`Postingan ${getSourceName(item.sumber)} dianalisis: ${getClassificationDisplay(item.label).text} (${item.lokasi_nama})`);
        fetchStats();
        return;
      }
    } catch (err) {
      console.error('[!] Error inspect social url:', err);
      if (btnPlotText) btnPlotText.innerText = 'Analisis teks & cari sumber terkait';
    }
  }

    if (btnPlotText) btnPlotText.innerText = 'Menganalisis teks...';

  try {
    const res = await fetch('/api/analyze', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text, save: saveToMap })
    });
    const data = await res.json();
    if (btnPlotText) btnPlotText.innerText = 'Analisis teks & cari sumber terkait';

    if (data.success && data.analysis) {
      const a = data.analysis;
      AppState.lastAnalysis = a;

      // Tampilkan Result Box di Modal
      const resultBox = document.getElementById('checker-result-box');
      if (resultBox) resultBox.classList.remove('hidden');

      // Update Badge Klasifikasi
      const unconfirmed = a.verifikasi_status !== 'terverifikasi' ||
        /prediksi|belum terkonfirmasi|belum terverifikasi/i.test(a.analysis_basis || '');
      const badge = document.getElementById('res-badge');
      if (badge) {
        const display = getClassificationDisplay(a.label);
        const labelText = display.text;
        const labelColor = display.color;
        if (labelColor === 'red') {
          badge.className = 'px-2.5 py-0.5 rounded text-xs font-semibold bg-red-50 text-red-700 border border-red-200';
        } else if (labelColor === 'blue') {
          badge.className = 'px-2.5 py-0.5 rounded text-xs font-semibold bg-blue-50 text-blue-700 border border-blue-200';
        } else {
          badge.className = 'px-2.5 py-0.5 rounded text-xs font-semibold bg-slate-100 text-slate-700 border border-slate-300';
        }
        badge.innerText = unconfirmed
          ? `Prediksi sementara · ${labelText}`
          : labelText;
      }

      // Update Confidence Bar
      const confPercent = Math.round(a.probabilitas * 100);
      const confEl = document.getElementById('res-confidence');
      if (confEl) confEl.innerText = `${confPercent}%`;
      const bar = document.getElementById('res-prob-bar');
      if (bar) {
        bar.style.width = `${confPercent}%`;
        bar.className = `h-2 rounded-full transition-all duration-500 ${a.label === 'hoaks' ? 'bg-red-500' : (a.label === 'fakta' ? 'bg-blue-500' : 'bg-slate-500')}`;
      }

      const basisEl = document.getElementById('res-analysis-basis');
      if (basisEl) basisEl.textContent = a.label === 'negatif'
        ? 'Bukti yang ditemukan belum cukup untuk menetapkan klaim sebagai fakta atau hoaks.'
        : (a.analysis_basis || 'Prediksi dari isi teks; belum ada dasar sumber yang ditampilkan.');
      const sourceList = document.getElementById('res-related-sources');
      if (sourceList) {
        sourceList.replaceChildren();
        const sourceHeading = document.createElement('p');
        sourceHeading.className = 'text-xs font-semibold text-slate-700';
        sourceHeading.textContent = `Berita dan sumber yang dibandingkan (${a.related_sources?.length || 0})`;
        sourceList.appendChild(sourceHeading);
        if (a.related_sources?.length) {
          a.related_sources.forEach((source) => {
            let url;
            try {
              url = new URL(source.url);
              if (!['http:', 'https:'].includes(url.protocol)) return;
            } catch (_) { return; }
            const link = document.createElement('a');
            link.href = url.href;
            link.target = '_blank';
            link.rel = 'noopener noreferrer';
            link.className = 'block rounded-md app-surface-soft px-2 py-1.5 text-xs text-slate-800 hover:text-slate-900';
            const sourceLabel = getClassificationDisplay(source.label);
            const label = source.label ? ` · ${sourceLabel.text}` : '';
            const sourceStatus = source.verification_status === 'terverifikasi' ? ' · Diperiksa' : ' · Prediksi sementara';
            const type = source.content_type === 'postingan' ? 'Media sosial' : 'Berita';
            const match = Number.isFinite(Number(source.relevance)) ? ` · kecocokan ${Math.round(source.relevance * 100)}%` : '';
            link.textContent = `${type}: ${source.source || source.domain} — ${source.title}${label}${sourceStatus}${match}`;
            sourceList.appendChild(link);
          });
        } else {
          const noSource = document.createElement('p');
          noSource.className = 'text-xs text-slate-500';
          noSource.textContent = 'Belum ditemukan berita atau postingan yang cukup cocok. Keyakinan label diturunkan.';
          sourceList.appendChild(noSource);
        }
      }

      // Update Info Episentrum Pembuat (Origin)
      const originName = a.origin ? a.origin.nama : (a.lokasi ? a.lokasi.nama : 'Tidak disebut dalam teks');
      const originEl = document.getElementById('res-origin');
      if (originEl) originEl.innerText = originName;

      // Update Info Titik-Titik Penyebar (Spreaders)
      const spreadersList = document.getElementById('res-spreaders-list');
      if (spreadersList) {
        spreadersList.innerHTML = a.spreaders && a.spreaders.length > 0 ? a.spreaders.map(s => {
          const cName = s.nama.split('(')[0].trim();
          return `<span class="px-2 py-0.5 rounded bg-slate-100 text-slate-800 border border-slate-300 text-xs">${cName}</span>`;
        }).join('') : '<span class="text-slate-500 text-xs">Tidak ada data sebaran untuk teks ini.</span>';
      }

      // Update Link Rujukan Berita Terkait Langsung
      const newsLinkEl = document.getElementById('res-news-link');
      const newsLinkLabel = document.getElementById('res-news-link-label');
      const newsUrl = a.url || getRelatedNewsUrl({ teks: a.teks, label: a.label, sumber: 'cek_mandiri' });
      if (newsLinkEl && newsUrl) {
        newsLinkEl.href = newsUrl;
        if (newsLinkLabel) {
          newsLinkLabel.innerText = a.url_type === 'search'
            ? 'Cari sumber dari teks ini ↗'
            : 'Buka sumber asli ↗';
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

        // Hitung batas peta dari titik lokasi yang benar-benar disebutkan.
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

          // Arahkan kamera ke lokasi yang benar-benar disebutkan.
          if (AppState.markersVisible && bounds.isValid()) {
            AppState.map.fitBounds(bounds, { padding: [60, 60], maxZoom: 8, duration: 1.5 });
          }

          // Buka popup pada episentrum pembuat
          setTimeout(() => {
            if (AppState.markersVisible && originId && AppState.markers.has(originId)) {
              AppState.markers.get(originId).openPopup();
            }
          }, 1200);
        }, 2000);

        // Perbarui statistik dashboard setelah data tersimpan.
        fetchStats();
      } else if (saveToMap) {
        showToast('Lokasi tidak disebut dalam teks, jadi hasil tidak ditambahkan ke peta.');
      }
    } else {
      alert(data.error || 'Gagal menganalisis teks.');
    }
  } catch (err) {
    if (btnPlotText) btnPlotText.innerText = 'Analisis teks & cari sumber terkait';
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
    wsIndicator.className = 'w-2 h-2 rounded-full bg-slate-500';
    wsStatus.className = 'text-xs text-slate-600 font-medium';
    wsStatus.innerText = 'Terhubung';
  });

  AppState.socket.on('disconnect', () => {
    wsIndicator.className = 'w-2 h-2 rounded-full bg-red-500';
    wsStatus.className = 'text-xs text-red-700 font-medium';
    wsStatus.innerText = 'Tidak terhubung';
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
    console.log('[🔔] Pukul 00:00 WIB: Rollover jendela 24 jam:', data);
    // Bersihkan marker lama
    AppState.threats.clear();
    AppState.markers.forEach(m => AppState.map.removeLayer(m));
    AppState.markers.clear();
    renderFeedList();
    fetchInitialThreats();
    fetchStats();
    loadProvinces();
    showToast(data.message || 'Pukul 00:00 WIB: Jendela pemantauan bergulir ke 24 jam terakhir.');
  });

  // Event saat data kadaluarsa (>24 jam) dibersihkan dari server
  AppState.socket.on('threats_purged', (data) => {
    console.log('[🧹] Data kadaluarsa >24 jam otomatis disingkirkan dari peta:', data);
    fetchInitialThreats();
    fetchStats();
    loadProvinces();
    showToast(`Pembersihan otomatis: ${data.count || 0} berita yang lewat >24 jam dihilangkan dari peta.`);
  });

  AppState.socket.on('viral_engine_ready', (info) => {
    console.log('[✓]', info.message, info.rollover_schedule);
  });
}

// Pemeriksa kadaluarsa klien: memastikan bila ada berita > 24 jam langsung hilang dari peta
function purgeExpiredThreatsClient() {
  const cutoff = Date.now() - (24 * 60 * 60 * 1000); // 24 jam
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
    console.log(`[🧹 Purge Klien] ${purgedCount} titik berita >24 jam otomatis dihilangkan dari peta.`);
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

// Memuat Direktori 38 Provinsi beserta statistik berita viral 24 jam terakhir
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
        selectProv.innerHTML = '<option value="all">Semua provinsi</option>';
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
      <div class="col-span-full py-12 text-center text-slate-500 text-xs">
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
        ? 'bg-slate-100 border-slate-300'
        : 'app-surface border-slate-200 hover:border-slate-300 hover:bg-slate-200'
    }`;

    card.innerHTML = `
      <div class="flex items-start justify-between mb-2">
        <div class="flex items-center space-x-2">
          <div class="w-7 h-7 rounded-lg bg-slate-100 group-hover:bg-slate-200 border border-slate-300 group-hover:border-slate-300 flex items-center justify-center shrink-0 transition">
            <i data-lucide="map-pin" class="w-3.5 h-3.5 ${isActive ? 'text-slate-700' : 'text-slate-500 group-hover:text-slate-900'}"></i>
          </div>
          <div>
            <h4 class="text-xs font-bold text-slate-900 group-hover:text-slate-900 transition">${prov.provinsi}</h4>
            <span class="text-xs text-slate-500">Data 24 jam terakhir</span>
          </div>
        </div>
        <span class="px-2 py-0.5 rounded-full text-xs font-semibold ${
          prov.total > 0 ? 'bg-slate-100 text-slate-700 border border-slate-300' : 'bg-slate-100 text-slate-500'
        }">
          ${prov.total} berita
        </span>
      </div>

      <!-- Kategori Breakdown Badges -->
      <div class="flex items-center space-x-1.5 text-xs pt-2 border-t border-slate-200">
        <span class="px-1.5 py-0.5 rounded bg-red-50 border border-red-200 text-red-700 font-semibold" title="Hoaks">
          Hoaks ${prov.hoaks || 0}
        </span>
        <span class="px-1.5 py-0.5 rounded bg-blue-50 border border-blue-200 text-blue-700 font-semibold" title="Fakta">
          Fakta ${prov.fakta || 0}
        </span>
        <div class="flex-1 text-right">
          <span class="text-xs text-slate-700 group-hover:text-slate-900 font-semibold flex items-center justify-end space-x-0.5">
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
  showToast(`Peta menampilkan berita dari ${prov.provinsi} (${prov.total} berita).`);
}

// ==========================================
// 8. EVENT LISTENERS UI & PENCARIAN
// ==========================================

function renderSearchResults(data) {
  const panel = document.getElementById('search-results');
  if (!panel) return;
  panel.replaceChildren();
  panel.classList.remove('hidden');

  const heading = document.createElement('div');
  heading.className = 'sticky top-0 z-10 border-b border-slate-200 app-surface px-4 py-3';
  const summary = document.createElement('div');
  summary.className = 'flex items-center justify-between gap-3';
  const title = document.createElement('div');
  title.className = 'text-xs font-bold text-slate-900';
  title.textContent = data.count
    ? `${data.count} berita dan sumber pemeriksa fakta yang cocok`
    : 'Belum ditemukan berita cocok dari penerbit tepercaya';
  summary.appendChild(title);
  if (data.summary_label) {
    const badge = document.createElement('span');
    const label = data.summary_label;
    const isPrediction = /prediksi|belum terkonfirmasi|belum terverifikasi/i.test(data.summary_basis || '');
    const display = getClassificationDisplay(label);
    const color = label === 'hoaks' ? 'text-red-800 border-red-200 bg-red-50'
      : label === 'fakta' ? 'text-blue-800 border-blue-200 bg-blue-50'
        : 'text-slate-700 border-slate-300 bg-slate-100';
    badge.className = `shrink-0 rounded-full border px-2.5 py-1 text-xs font-semibold ${color}`;
    badge.textContent = isPrediction ? `Prediksi sementara · ${display.text}` : display.text;
    summary.appendChild(badge);
  }
  const note = document.createElement('p');
  note.className = 'mt-1 text-xs leading-relaxed text-slate-500';
  note.textContent = data.message || 'Label dihitung dari isi, bukan judul saja. Buka tautan untuk melihat sumber asli.';
  if (data.summary_label === 'negatif') {
    note.textContent = 'Bukti yang ditemukan belum cukup untuk menetapkan klaim sebagai fakta atau hoaks.';
  } else if (data.summary_basis) {
    const confidence = Math.round((data.summary_confidence || 0) * 100);
    note.textContent += ` Dasar prediksi: ${data.summary_basis} (${confidence}% perkiraan).`;
  }
  note.textContent += ' Prediksi dari AI bisa saja ada kekeliruan. Periksa sumber aslinya.';
  if (data.summary_label === 'fakta' && !data.articles?.some(article => /turnbackhoax|cekfakta/i.test(article.domain || ''))) {
    note.textContent += ' Label fakta merujuk pada laporan dari penerbit tepercaya; klaim khusus hanya dipastikan jika ada hasil pemeriksa fakta yang cocok.';
  }
  heading.append(summary, note);
  panel.appendChild(heading);

  if (!data.articles || data.articles.length === 0) {
    const empty = document.createElement('p');
    empty.className = 'px-4 py-5 text-xs text-slate-500';
    empty.textContent = 'Coba variasi kata kunci. Pencarian memeriksa isi berita dari berbagai penerbit dan sumber pemeriksa fakta.';
    panel.appendChild(empty);
    return;
  }

  const list = document.createElement('div');
  list.className = 'divide-y divide-slate-200';
  data.articles.forEach((article) => {
    let sourceUrl;
    try {
      sourceUrl = new URL(article.url);
      if (!['http:', 'https:'].includes(sourceUrl.protocol)) return;
    } catch (_) {
      return;
    }

    const card = document.createElement('article');
    card.className = 'px-4 py-3 hover:bg-slate-100';
    const top = document.createElement('div');
    top.className = 'flex items-start justify-between gap-3';
    const articleTitle = document.createElement('a');
    articleTitle.className = 'text-xs font-semibold leading-relaxed text-slate-800 hover:text-slate-900';
    articleTitle.href = article.url;
    articleTitle.target = '_blank';
    articleTitle.rel = 'noopener noreferrer';
    articleTitle.textContent = article.title || 'Artikel tanpa judul';
    const label = article.analysis?.label || 'fakta';
    const display = getClassificationDisplay(label);
    const badge = document.createElement('span');
    const color = label === 'hoaks' ? 'text-red-800 border-red-200 bg-red-50'
      : label === 'fakta' ? 'text-blue-800 border-blue-200 bg-blue-50'
        : 'text-slate-700 border-slate-300 bg-slate-100';
    badge.className = `shrink-0 rounded-full border px-2 py-0.5 text-xs font-semibold ${color}`;
    badge.textContent = display.text;
    top.append(articleTitle, badge);

    const sourceLink = document.createElement('a');
    sourceLink.className = 'mt-1 inline-flex items-center gap-1 text-xs font-semibold text-slate-700 hover:text-slate-900';
    sourceLink.href = sourceUrl.href;
    sourceLink.target = '_blank';
    sourceLink.rel = 'noopener noreferrer';
    const sourceName = article.source || article.domain || sourceUrl.hostname.replace(/^www\./, '');
    sourceLink.textContent = `Sumber: ${sourceName} (${sourceUrl.hostname.replace(/^www\./, '')}) · Buka sumber asli ↗`;

    const metadata = document.createElement('p');
    metadata.className = 'mt-1 text-xs text-slate-500';
    const confidence = Math.round((article.analysis?.probabilitas || 0) * 100);
    const basis = label !== 'negatif' && article.analysis?.basis ? ` · dasar prediksi: ${article.analysis.basis}` : '';
    metadata.textContent = `${article.content_type === 'postingan' ? 'Isi postingan' : 'Isi artikel'} dianalisis · kecocokan ${Math.round((article.relevance || 0) * 100)}% · perkiraan ${confidence}%${basis}`;

    const bodyToggle = document.createElement('details');
    bodyToggle.className = 'mt-2';
    const bodyLabel = document.createElement('summary');
    bodyLabel.className = 'cursor-pointer text-xs font-semibold text-slate-700 hover:text-slate-900';
    bodyLabel.textContent = `Tampilkan isi yang dianalisis (${article.content_length || article.content?.length || 0} karakter)`;
    const body = document.createElement('p');
    body.className = 'mt-2 max-h-56 overflow-y-auto whitespace-pre-line rounded-lg app-surface-soft p-2.5 text-xs leading-relaxed text-slate-700';
    body.textContent = article.content || 'Isi tidak tersedia.';
    bodyToggle.append(bodyLabel, body);
    card.append(top, sourceLink, metadata, bodyToggle);
    list.appendChild(card);
  });
  panel.appendChild(list);
}

async function searchNewsByKeyword(query) {
  const panel = document.getElementById('search-results');
  if (!panel) return;
  document.getElementById('feed-container')?.classList.add('hidden');
  panel.classList.remove('hidden');
  panel.textContent = 'Mencari artikel/cuitan dari sumber tepercaya dan membaca isi lengkapnya…';
  panel.className = 'absolute left-0 top-full mt-2 w-[min(92vw,48rem)] max-h-[72vh] overflow-y-auto rounded-lg border border-slate-300 app-surface p-4 text-xs text-slate-800 shadow-lg z-[80]';

  try {
    const response = await fetch(`/api/search/news?q=${encodeURIComponent(query)}`);
    const data = await response.json();
    if (!response.ok || !data.success) throw new Error(data.error || 'Pencarian berita gagal.');
    renderSearchResults(data);
  } catch (error) {
    panel.textContent = error.message || 'Tidak dapat mencari berita saat ini.';
    panel.classList.add('text-red-800');
    console.error('[!] Pencarian berita gagal:', error);
  }
}

function initUIEvents() {
  const themeToggle = document.getElementById('theme-toggle');
  const applyTheme = (theme, persist = true) => {
    const isDark = theme === 'dark';
    document.documentElement.dataset.theme = isDark ? 'dark' : 'light';
    if (persist) {
      try { localStorage.setItem('geosentry-theme', isDark ? 'dark' : 'light'); } catch (_) {}
    }

    if (themeToggle) {
      themeToggle.setAttribute('aria-pressed', String(isDark));
      themeToggle.setAttribute('aria-label', `Aktifkan mode ${isDark ? 'terang' : 'gelap'}`);
      themeToggle.title = `Aktifkan mode ${isDark ? 'terang' : 'gelap'}`;
      themeToggle.innerHTML = `
        <i data-lucide="${isDark ? 'sun' : 'moon'}" class="w-4 h-4"></i>
        <span id="theme-toggle-label" class="hidden 2xl:inline text-xs font-semibold">Mode ${isDark ? 'terang' : 'gelap'}</span>
      `;
      if (window.lucide?.createIcons) lucide.createIcons();
    }

    if (AppState.chartCategory) {
      AppState.chartCategory.options.plugins.legend.labels.color = isDark ? '#cbd5e1' : '#475569';
      AppState.chartCategory.update('none');
    }
  };

  applyTheme(document.documentElement.dataset.theme || 'light', false);
  themeToggle?.addEventListener('click', () => {
    applyTheme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark');
  });

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
    document.getElementById('search-results')?.classList.add('hidden');
    document.getElementById('feed-container')?.classList.remove('hidden');
    applyFilters();
  });

  // Enter mencari artikel/cuitan dari sumber aktual, termasuk padanan kata.
  inputSearch.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      const query = inputSearch.value.trim();
      if (query) searchNewsByKeyword(query);
    }
  });

  document.addEventListener('click', (event) => {
    const results = document.getElementById('search-results');
    if (results && !results.contains(event.target) && !inputSearch.contains(event.target)) {
      results.classList.add('hidden');
      document.getElementById('feed-container')?.classList.remove('hidden');
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

  // Filter kategori
  document.getElementById('filter-label').onchange = (e) => {
    AppState.currentFilter.label = e.target.value;
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


  // Sakelar untuk menampilkan atau menyembunyikan semua titik radar.
  const btnMarkers = document.getElementById('layer-markers');
  if (btnMarkers) {
    btnMarkers.onclick = () => {
      AppState.markersVisible = !AppState.markersVisible;
      btnMarkers.setAttribute('aria-pressed', String(AppState.markersVisible));
      btnMarkers.className = AppState.markersVisible
        ? 'px-3 py-1.5 rounded-lg bg-green-700 text-green-50 font-semibold transition hover:bg-green-800 flex items-center space-x-1.5'
        : 'px-3 py-1.5 rounded-lg bg-green-50 border border-green-200 text-green-800 font-semibold transition hover:bg-green-100 flex items-center space-x-1.5';
      const stateLabel = btnMarkers.querySelector('span');
      if (stateLabel) stateLabel.textContent = `Titik peta: ${AppState.markersVisible ? 'Tampil' : 'Sembunyi'}`;
      applyFilters();
    };
  }

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
    toast.className = `px-4 py-2.5 rounded-lg shadow-lg text-xs font-semibold flex items-center space-x-2 border transition-all duration-300 transform translate-y-[-6px] opacity-0 pointer-events-auto ${
      isError 
        ? 'bg-red-50 text-red-800 border-red-200'
        : 'app-surface text-slate-800 border-slate-300'
    }`;
    toast.innerHTML = `
      <span class="w-2 h-2 rounded-full ${isError ? 'bg-red-400' : 'bg-slate-700'}"></span>
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
