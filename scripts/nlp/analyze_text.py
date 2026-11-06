"""
GeoSentry - Modul Analisis Cek Berita Mandiri (Instant Fact-Checker)
Menggabungkan inferensi NLP SGDClassifier dan Geocoding Wilayah Indonesia.
Menerima teks bebas, menghasilkan label, probabilitas, skor, dan koordinat spasial.
"""

import os
import sys
import json
import argparse

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from scripts.nlp.predict import predict_threat
from scripts.gazetteer.geocode import extract_location


def analyze(text: str) -> dict:
    if not text or not text.strip():
        return {
            "error": "Teks tidak boleh kosong",
            "label": "fakta",
            "probabilitas": 0.5,
            "lokasi": None
        }

    # 1. Prediksi AI
    pred = predict_threat(text)

    # 2. Ekstraksi Lokasi
    loc = extract_location(text, default_if_none=True)

    return {
        "teks": text.strip(),
        "label": pred["label"],
        "probabilitas": pred["probabilitas"],
        "skor": pred["skor"],
        "lokasi": loc
    }


def main():
    parser = argparse.ArgumentParser(description="Analisis Berita & Cek Fakta Mandiri")
    parser.add_argument("text", nargs="*", help="Teks berita atau cuitan yang ingin dianalisis")
    parser.add_argument("--json", action="store_true", help="Output format JSON murni")
    args = parser.parse_args()

    input_text = " ".join(args.text).strip()
    if not input_text:
        # Jika melalui stdin
        if not sys.stdin.isatty():
            input_text = sys.stdin.read().strip()

    result = analyze(input_text)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
