"""
GeoSentry - Script Simulator Buffer Real-Time (Hari 19-20)
Strategi Presentasi Lomba: 'Simulasi Buffer Real-Time Anti-Gagal'
Menyalurkan data secara berkala dari PostgreSQL dengan mengubah status_tayang = TRUE.
Memicu siaran WebSocket secara instan tanpa scraping live saat pitch presentasi di depan juri.
"""

import os
import sys
import time
import argparse
import requests
from dotenv import load_dotenv
import psycopg

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(dotenv_path=os.path.join(BASE_DIR, ".env"))

DATABASE_URL = os.getenv("DATABASE_URL")
BACKEND_PORT = os.getenv("PORT", "3000")
BACKEND_URL = f"http://localhost:{BACKEND_PORT}"


def get_status():
    """Mengambil status data dari database."""
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM social_threats;")
            total = cur.fetchone()[0]

            cur.execute("SELECT count(*) FROM social_threats WHERE status_tayang = TRUE;")
            active = cur.fetchone()[0]

            cur.execute("""
                SELECT label, count(*) FROM social_threats 
                WHERE status_tayang = TRUE 
                GROUP BY label;
            """)
            breakdown = dict(cur.fetchall())

            print("=" * 55)
            print("  🛡️  GEOSENTRY DATA GENERATOR & SIMULATOR STATUS")
            print("=" * 55)
            print(f"  Total Titik di Database : {total}")
            print(f"  Titik Aktif di Peta     : {active}")
            print(f"  Rincian Kategori        : Hoaks={breakdown.get('hoaks', 0)} | Fakta={breakdown.get('fakta', 0)} | Negatif={breakdown.get('negatif', 0)}")
            print("=" * 55)
            return {"total": total, "active": active}


def reset_buffer():
    """Mereset data kembali ke baseline 120 titik nasional."""
    print("[*] Mereset data kembali ke baseline 120 titik nasional...")
    try:
        # Coba reset lewat Backend API jika server aktif agar WebSocket memancarkan reset
        res = requests.post(f"{BACKEND_URL}/api/simulator/reset", timeout=2)
        if res.status_code == 200:
            print("[✓] Buffer berhasil di-reset via Backend API (WebSocket tersinkronisasi).")
            return
    except Exception:
        pass

    # Fallback langsung ke database
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE social_threats SET status_tayang = FALSE, dikoreksi = FALSE;")
            conn.commit()
            print("[✓] Buffer berhasil di-reset langsung ke PostgreSQL.")


def step_simulation():
    """Mengaktifkan 1 baris data berikutnya dari buffer."""
    try:
        # Panggil via Backend API agar Socket.io memancarkan data secara real-time
        res = requests.post(f"{BACKEND_URL}/api/simulator/step", timeout=2)
        if res.status_code == 200:
            data = res.json()
            if data.get("success"):
                p = data["feature"]["properties"]
                label_icon = "🔴 HOAKS" if p["label"] == "hoaks" else ("🔵 FAKTA" if p["label"] == "fakta" else "🟡 NEGATIF")
                print(f"[+] [TAYANG] ID {p['id']} | {label_icon} | 📍 {p['lokasi_nama']} ({p['sumber'].upper()})")
                print(f"    Teks: {p['teks'][:85]}...")
                return True
            else:
                print(f"[i] {data.get('message', 'Buffer habis.')}")
                return False
    except requests.exceptions.ConnectionError:
        print("[!] Gagal terhubung ke Backend Node.js! Pastikan server berjalan (npm start di folder backend).")
        return False
    except Exception as e:
        print(f"[!] Error saat step simulasi: {e}")
        return False


def run_continuous(interval=2.5, max_steps=None):
    """Menjalankan simulasi injeksi data berkala secara otomatis."""
    print("=" * 60)
    print("  🚀 MEMULAI SIMULATOR ALIRAN DATA BERKALA ANTI-GAGAL")
    print(f"  ⏱️  Interval Tiap Injeksi : {interval} detik")
    print(f"  🎯 Target URL Backend    : {BACKEND_URL}")
    print("  💡 Tekan Ctrl+C untuk menghentikan simulasi kapan saja.")
    print("=" * 60)

    steps = 0
    try:
        while True:
            success = step_simulation()
            if not success:
                print("[i] Buffer kosong atau tidak ada data baru yang dapat ditayangkan.")
                break
            
            steps += 1
            if max_steps and steps >= max_steps:
                print(f"[✓] Mencapai batas langkah maksimal ({max_steps} kali). Selesai.")
                break

            time.sleep(interval)
    except KeyboardInterrupt:
        print("\n[!] Simulasi dihentikan oleh pengguna.")


def main():
    parser = argparse.ArgumentParser(description="GeoSentry Presentation Buffer Simulator")
    parser.add_argument("--interval", type=float, default=2.5, help="Interval injeksi data dalam detik (default: 2.5)")
    parser.add_argument("--step", action="store_true", help="Jalankan 1 langkah simulasi saja")
    parser.add_argument("--count", type=int, default=None, help="Jumlah langkah yang akan dijalankan sebelum berhenti")
    parser.add_argument("--reset", action="store_true", help="Reset seluruh status_tayang ke FALSE")
    parser.add_argument("--status", action="store_true", help="Tampilkan status ringkasan buffer saat ini")

    args = parser.parse_args()

    if args.reset:
        reset_buffer()
        get_status()
    elif args.status:
        get_status()
    elif args.step:
        step_simulation()
    else:
        run_continuous(interval=args.interval, max_steps=args.count)


if __name__ == "__main__":
    main()
