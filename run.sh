#!/usr/bin/env bash
# ==============================================================
# GeoSentry - One-Click Launcher
# ==============================================================
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
cd "$DIR"

echo "======================================================"
echo "  🛡️  MEMULAI GEOSENTRY - CYBER THREAT WEBGIS"
echo "======================================================"

# 1. Pastikan PostgreSQL Aktif
if ! pg_isready -h localhost -p 5432 >/dev/null 2>&1; then
    echo "[!] PostgreSQL belum aktif. Menyalakan cluster PostgreSQL..."
    sudo pg_ctlcluster 18 main start || echo "[!] Mohon pastikan PostgreSQL service aktif."
fi

# 2. Cek Virtual Environment Python
if [ -d ".venv" ]; then
    echo "[✓] Virtual Environment Python ditemukan (.venv)"
    source .venv/bin/activate
fi

# 3. Jalankan Server Node.js
echo "[✓] Menjalankan Server Backend & WebSockets..."
echo "🌐 Buka browser Anda di: http://localhost:3000"
echo "------------------------------------------------------"
node backend/server.js
