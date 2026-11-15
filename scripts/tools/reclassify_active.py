"""Klasifikasikan ulang data aktif yang belum dikoreksi dengan tiga label model."""
import os, sys, json

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, BASE_DIR)

from dotenv import load_dotenv
load_dotenv(os.path.join(BASE_DIR, ".env"))

import psycopg
from scripts.nlp.predict import predict_threat

DATABASE_URL = os.getenv("DATABASE_URL")

def main():
    label_counts = {"hoaks": 0, "fakta": 0, "negatif": 0}
    total = 0

    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            # Ambil semua berita aktif 24 jam
            cur.execute("""
                SELECT id, teks, label
                FROM social_threats
                WHERE status_tayang = TRUE 
                  AND dibuat_pada >= (NOW() - INTERVAL '24 hours')
                  AND dikoreksi = FALSE
                ORDER BY id;
            """)
            rows = cur.fetchall()
            total = len(rows)
            print(f"[+] Re-classifying {total} berita aktif...")

            for row in rows:
                threat_id, teks, old_label = row

                # Prediksi ulang dengan model yang sudah diperbaiki
                pred = predict_threat(teks)
                new_label = pred["label"]
                new_prob = pred["probabilitas"]

                if new_label != old_label:
                    cur.execute(
                        "UPDATE social_threats SET label = %s, probabilitas = %s WHERE id = %s;",
                        (new_label, new_prob, threat_id)
                    )
                label_counts[new_label] += 1

            conn.commit()

    print(f"\n[✓] Re-classification selesai:")
    print(f"  Total diproses: {total}")
    for label, count in label_counts.items():
        print(f"  {label}: {count}")

if __name__ == "__main__":
    main()
