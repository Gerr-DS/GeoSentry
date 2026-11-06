"""
GeoSentry - Modul Inferensi & Feedback Loop AI (Hybrid Intelligence)
1. Memprediksi ancaman siber sosial (hoaks, fakta, negatif) menggunakan kombinasi:
   - Statistical Incremental ML (SGDClassifier + HashingVectorizer, Stateless & Zero-Token)
   - Knowledge Base Narasi Disinformasi Terverifikasi (TurnBackHoax/CekFakta Patterns)
2. Memfasilitasi Online Learning (partial_fit) ketika analis mengoreksi hasil di dashboard.
"""

import os
import re
import sys
import json
import joblib
import numpy as np
from .train import preprocess_text, CLASSES, MODEL_PKL

# Global cache model
_MODEL_BUNDLE = None

# ==============================================================
# KNOWLEDGE BASE INDIKATOR DISINFORMASI (TurnBackHoax / CekFakta)
# ==============================================================
HOAX_INDICATOR_PATTERNS = [
    # Isu Ijazah, Pemilu & Konspirasi Politik
    re.compile(r"\b(?:ijazah\s+(?:jokowi\s+)?palsu|jokowi\s+ijazah\s+palsu)\b", re.I),
    re.compile(r"\b(?:server\s+kpu\s+diretas|suara\s+dialihkan\s+100\s+persen|kpu\s+curang)\b", re.I),
    re.compile(r"\b(?:menteri|pejabat|mentan|menkeu|menkopolhukam|menag)\b.*\b(?:mundur|mengundurkan\s+diri)\b", re.I),
    re.compile(r"\b(?:bumi\s+(?:itu\s+)?datar|konspirasi\s+elit\s+global|pendaratan\s+bulan\s+sandiwara|chemtrail)\b", re.I),
    re.compile(r"\b(?:china|asing|komunis|tentara)\b.*\b(?:kirim\s+tentara|tentara\s+asing|mendarat\s+di\s+indonesia|ambil\s+alih)\b", re.I),
    re.compile(r"\b(?:surat\s+edaran\s+palsu|pencopotan\s+jabatan\s+rahasia)\b", re.I),

    # Isu Makanan, Minuman, Pseudosains & Urban Legend
    re.compile(r"\b(?:beras\s+plastik|telur\s+(?:palsu|plastik|karet)|garam\s+(?:kaca|beracun)|sintetis\s+berbahan\s+lem)\b", re.I),
    re.compile(r"\bsusu\s+beruang\b.*\b(?:racun|penyakit|sembuh|netralisir|paru)\b", re.I),
    re.compile(r"\b(?:air\s+rebusan|ramuan|bawang\s+putih|bawang\s+merah|daun\s+sirsak|kelapa\s+hijau|jeruk\s+nipis|garam\s+kasar|air\s+hangat)\b.*\b(?:sembuh|bunuh|kanker|stroke|diabetes|tumor|covid|corona|racun)\b", re.I),
    re.compile(r"\b(?:sembuh(?:kan)?|matikan|bunuh|cegah)\s+(?:semua\s+)?(?:kanker|tumor|stroke|covid|corona|virus|diabetes|hiv|aids)\b", re.I),
    re.compile(r"\b(?:darah\s+(?:langsung\s+)?membeku|serangan\s+jantung\s+mendadak)\b.*\b(?:minum\s+air\s+es|gorengan)\b", re.I),
    re.compile(r"\bvaksina?.*\b(?:microchip|chip|5g|racun|pembunuh|mandul|lumpuh|darah\s+babi|magnet)\b", re.I),
    re.compile(r"\b(?:meteran\s+listrik|token\s+pln)\b.*\b(?:sadap|chip\s+asing|mata-mata)\b", re.I),
    re.compile(r"\bulat\s+beracun\b.*\b(?:mematikan|pohon\s+mangga|mati\s+dalam|jam)\b", re.I),

    # Isu Keuangan Palsu, Phishing & Bansos
    re.compile(r"\b(?:uang\s+(?:pecahan\s+)?(?:100|50|75)\s*ribu\s+ditarik\s+bi|(?:ditarik|uang ditarik)\s+bi)\b", re.I),
    re.compile(r"\b(?:pemutihan|hapus)\s+(?:seluruh\s+)?(?:hutang|utang)\s+pinjol\b", re.I),
    re.compile(r"\b(?:uang\s+koin|koin\s+seribu|uang\s+kuno)\b.*\b(?:kelapa\s+sawit|melati|ratusan\s+juta|dibeli)\b", re.I),
    re.compile(r"\b(?:surat\s+pengangkatan|penerimaan)\s+(?:pns|cpns|honorer)\b.*\b(?:tanpa\s+tes|bkn)\b", re.I),
    re.compile(r"\b(?:bagi-?bagi|bagikan|pembagian)\s+(?:bansos|blt|subsidi|saldo|uang|sepeda|mobil|pulsa|kuota|hadiah)\b", re.I),
    re.compile(r"\b(?:klaim|daftar|ambil)\s+(?:saldo|bantuan|bansos|kuota|dana\s+kaget|hadiah|voucher)\b", re.I),
    re.compile(r"\b(?:t\.me|bit\.ly|s\.id|wa\.me)/[a-zA-Z0-9_-]+\b", re.I),
    re.compile(r"\b(?:undian|pemenang)\s+(?:berhadiah|resmi)\b.*\b(?:alphard|uang\s+tunai|tanpa\s+diundi|hubungi\s+wa)\b", re.I),

    # Bencana & Kepanikan Palsu
    re.compile(r"\b(?:megathrust|tsunami|gempa\s+dahsyat|topan\s+tornado)\b.*\b(?:malam\s+ini|dini\s+hari|jam\s+\d|tenggelamkan|keluar\s+rumah|ungsi|sapu\s+bersih)\b", re.I),
    re.compile(r"\b(?:letusan|gunung\s+berapi)\b.*\b(?:gas\s+beracun\s+mematikan|ungsi\s+sekarang)\b", re.I),
    re.compile(r"\b(?:penculikan\s+anak|diculik)\b.*\b(?:mobil\s+(?:box\s+)?putih|organ\s+tubuh|badut)\b", re.I),

    # Sensasionalisme & Desakan Forward WA
    re.compile(r"\b(?:sebar(?:kan)?\s+sebelum\s+dihapus|info\s+a1\b|jangan\s+berhenti\s+di\s+(?:kamu|anda)|bagikan\s+ke\s+(?:semua\s+)?grup|viralkan\s+sekarang|jangan\s+mau\s+dibohongi\s+media)\b", re.I),
    re.compile(r"\b(?:hoaks|hoax|berita\s+bohong|kabar\s+dusta|fitnah\s+keji)\b", re.I),
]

FACT_INDICATOR_PATTERNS = [
    # Pejabat & Aksi Pembangunan
    re.compile(r"\b(?:presiden|menteri|kementerian|pemprov|pemkot|pemkab|gubernur|wali\s+kota|bupati)\b.*\b(?:meresmikan|meninjau|mengalokasikan|meluncurkan|menandatangani|melantik|membuka)\b", re.I),
    re.compile(r"\b(?:meresmikan|peresmian)\s+(?:jalan\s+tol|jembatan|bendungan|gedung|fasilitas|rumah\s+sakit|pelabuhan|bandara|puskesmas)\b", re.I),
    re.compile(r"\b(?:kementerian|kemenkes|kemendikbud|kemenkeu|kemlu|kemenag|pupr|pemprov|pemkot|pemkab|dinas)\b.*\b(?:meresmikan|membangun|menyelesaikan|meninjau|mengumumkan|mengalokasikan|meluncurkan)\b", re.I),
    
    # Penegakan Hukum & Kamtibmas
    re.compile(r"\b(?:polda|polrestabes|polres|polri|satuan\s+narkoba|tim\s+resmob|aparat\s+kepolisian)\b.*\b(?:menangkap|membekuk|mengamankan|meringkus|menggagalkan|menyita|menertibkan)\b", re.I),
    re.compile(r"\b(?:kejaksaan|pengadilan)\b.*\b(?:melimpahkan|menyidangkan|menuntut|memvonis|menetapkan\s+tersangka)\b", re.I),
    
    # Bencana & Cuaca Resmi
    re.compile(r"\b(?:bmkg|stasiun\s+geofisika)\b.*\b(?:tidak\s+berpotensi\s+tsunami|magnitudo|gempa\s+susulan|prakiraan\s+cuaca|hujan)\b", re.I),
    re.compile(r"\b(?:banjir|tanah\s+longsor|angin\s+puting\s+beliung|kebakaran|gempa\s+bumi)\b.*\b(?:merendam|merusak|melanda|bpbd|tim\s+sar|mengungsi|evakuasi)\b", re.I),
    
    # Lembaga Ekonomi & Data
    re.compile(r"\b(?:bank\s+indonesia|bi-rate)\b.*\b(?:suku\s+bunga|bi-rate|inflasi|rupiah|cadangan\s+devisa|mempertahankan|menaikkan|menurunkan)\b", re.I),
    re.compile(r"\b(?:badan\s+pusat\s+statistik|bps|perum\s+bulog)\b.*\b(?:pertumbuhan\s+ekonomi|stok\s+cadangan|inflasi|mencatat|mengumumkan)\b", re.I),
    
    # Kesehatan & Olahraga
    re.compile(r"\b(?:kemenkes|dinas\s+kesehatan)\b.*\b(?:mengimbau|menggelar|mendistribusikan|vaksinasi|fogging|imunisasi)\b", re.I),
    re.compile(r"\b(?:timnas|atlet|kejuaraan|turnamen)\b.*\b(?:skor\s+\d+-\d+|babak\s+final|medali\s+emas|meraih\s+kemenangan|juara)\b", re.I),
    
    # Pernyataan Jurnalistik Resmi & Klarifikasi
    re.compile(r"\b(?:siaran\s+pers|keterangan\s+pers|konferensi\s+pers|rilis\s+resmi|laporan\s+terkini|warta\s+daerah)\b", re.I),
    re.compile(r"\b(?:turnbackhoax|mafindo|cekfakta)\s+(?:menegaskan|mengonfirmasi|mengklarifikasi|menyatakan)\b", re.I),
]

NEGATIVE_INDICATOR_PATTERNS = [
    re.compile(r"\b(?:serbu\s+dan\s+bakar|angkat\s+senjata|gulingkan\s+(?:penguasa|pemerintah)|amuk\s+massa|bom\s+molotov)\b", re.I),
    re.compile(r"\b(?:usir|bantai|habisi)\s+(?:semua\s+)?(?:etnis|kaum|kelompok|minoritas|pendatang)\b", re.I),
    re.compile(r"\b(?:sweeping\s+(?:malam|jalanan)|bawa\s+(?:sajam|celurit|golok)|tumpahkan\s+darah|perang\s+terbuka)\b", re.I),
    re.compile(r"\b(?:rezim\s+bangsat|aparat\s+keparat|darah\s+kalian\s+halal|ratakan\s+dengan\s+tanah)\b", re.I),
]


def load_model():
    """Memuat model dari disk ke memori cache."""
    global _MODEL_BUNDLE
    if _MODEL_BUNDLE is not None:
        return _MODEL_BUNDLE
    
    if not os.path.exists(MODEL_PKL):
        raise FileNotFoundError(f"Model {MODEL_PKL} belum dilatih! Jalankan train.py dulu.")
    
    _MODEL_BUNDLE = joblib.load(MODEL_PKL)
    return _MODEL_BUNDLE


def predict_threat(text: str) -> dict:
    """
    Memprediksi label ancaman (hoaks, fakta, negatif) dan probabilitas keyakinan.
    Menggunakan pendekatan Hybrid: Model Statistik SGDClassifier + Knowledge Base CekFakta.
    """
    if not text or not text.strip():
        return {
            "label": "fakta",
            "probabilitas": 0.5,
            "skor": {"fakta": 0.3333, "hoaks": 0.3333, "negatif": 0.3334}
        }

    bundle = load_model()
    vectorizer = bundle["vectorizer"]
    clf = bundle["classifier"]
    classes = list(clf.classes_)  # ["fakta", "hoaks", "negatif"]

    cleaned = preprocess_text(text)
    raw_lower = text.lower()

    # 1. Prediksi dasar model statistik
    X = vectorizer.transform([cleaned if cleaned else text])
    raw_probs = clf.predict_proba(X)[0]
    prob_dict = {classes[i]: float(raw_probs[i]) for i in range(len(classes))}

    # 2. Pengecekan Pola Khusus (Knowledge Base Heuristics)
    hoax_match_count = sum(1 for pat in HOAX_INDICATOR_PATTERNS if pat.search(raw_lower))
    fact_match_count = sum(1 for pat in FACT_INDICATOR_PATTERNS if pat.search(raw_lower))
    neg_match_count = sum(1 for pat in NEGATIVE_INDICATOR_PATTERNS if pat.search(raw_lower))

    # Kalibrasi skor secara adaptif
    if hoax_match_count > 0 and fact_match_count == 0:
        boost = min(0.45 * hoax_match_count, 0.85)
        prob_dict["hoaks"] += boost
        prob_dict["fakta"] = max(0.01, prob_dict["fakta"] - (boost * 0.75))
        prob_dict["negatif"] = max(0.01, prob_dict["negatif"] - (boost * 0.25))
    elif fact_match_count > 0 and hoax_match_count == 0:
        boost = min(0.40 * fact_match_count, 0.80)
        prob_dict["fakta"] += boost
        prob_dict["hoaks"] = max(0.01, prob_dict["hoaks"] - (boost * 0.75))
        prob_dict["negatif"] = max(0.01, prob_dict["negatif"] - (boost * 0.25))
    elif neg_match_count > 0:
        boost = min(0.45 * neg_match_count, 0.85)
        prob_dict["negatif"] += boost
        prob_dict["fakta"] = max(0.01, prob_dict["fakta"] - (boost * 0.5))
        prob_dict["hoaks"] = max(0.01, prob_dict["hoaks"] - (boost * 0.5))
    elif hoax_match_count > 0 and fact_match_count > 0:
        # Jika kedua pola cocok (misal: klarifikasi fakta hoaks)
        if "klarifikasi" in raw_lower or "turnbackhoax" in raw_lower or "cekfakta" in raw_lower:
            prob_dict["fakta"] += 0.35
            prob_dict["hoaks"] = max(0.02, prob_dict["hoaks"] - 0.25)
        else:
            prob_dict["hoaks"] += 0.35
            prob_dict["fakta"] = max(0.02, prob_dict["fakta"] - 0.25)

    # Normalisasi probabilitas agar jumlah total = 1.0
    total = sum(prob_dict.values())
    norm_scores = {c: round(prob_dict[c] / total, 4) for c in classes}

    # Cari kelas pemenang
    predicted_label = max(norm_scores, key=norm_scores.get)
    confidence = norm_scores[predicted_label]

    return {
        "label": predicted_label,
        "probabilitas": confidence,
        "skor": norm_scores
    }


def feedback_retrain(text: str, true_label: str) -> dict:
    """
    Online Learning (Incremental Feedback Loop):
    Memperbarui bobot model secara instan berdasarkan masukan koreksi analis.
    """
    global _MODEL_BUNDLE
    if true_label not in CLASSES:
        raise ValueError(f"Label '{true_label}' tidak valid! Harus salah satu dari {CLASSES}")

    bundle = load_model()
    vectorizer = bundle["vectorizer"]
    clf = bundle["classifier"]

    cleaned = preprocess_text(text)
    X = vectorizer.transform([cleaned if cleaned else text])

    # Update bobot model seketika
    clf.partial_fit(X, [true_label], classes=CLASSES)

    # Simpan kembali model yang telah diperbarui
    joblib.dump(bundle, MODEL_PKL)
    _MODEL_BUNDLE = bundle  # Update cache memori
    print(f"[✓] Feedback loop berhasil: Model diperbarui dengan label '{true_label}'")

    # Prediksi ulang untuk melihat perubahan skor
    re_pred = predict_threat(text)
    return {
        "status": "success",
        "updated_prediction": re_pred
    }


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--retrain":
        text_arg = sys.argv[2]
        label_arg = sys.argv[3] if len(sys.argv) > 3 else "fakta"
        res = feedback_retrain(text_arg, label_arg)
        print(json.dumps(res, indent=2))
    elif len(sys.argv) > 1:
        text_arg = " ".join(sys.argv[1:])
        res = predict_threat(text_arg)
        print(json.dumps(res, indent=2))
    else:
        test_samples = [
            "ijazah jokowi palsu",
            "beras plastik di pasar",
            "uang pecahan 100 ribu ditarik BI",
            "menteri mundur dari kabinet",
            "bumi itu datar konspirasi elit global",
            "Presiden Joko Widodo meresmikan jalan tol baru di Solo Jawa Tengah",
            "Banjir merendam ratusan rumah warga di Jakarta Selatan akibat hujan deras",
            "Timnas Indonesia menang melawan Vietnam dengan skor 2-0 di Stadion GBK",
        ]
        print("[+] Menguji Inferensi Hybrid NLP:")
        for sample in test_samples:
            result = predict_threat(sample)
            print(f"\nTeks  : {sample}")
            print(f"Hasil : Label={result['label'].upper()} | Conf={result['probabilitas']*100:.1f}% | Skor={result['skor']}")
