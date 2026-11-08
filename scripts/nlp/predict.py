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
    # Explicit hoax labels & forwards & TurnBackHoax debunking tags
    re.compile(r"\[(?:salah|disinformasi|manipulasi|penipuan|misinformasi|hoaks|hoax|palsu|fitnah)\]", re.I),
    re.compile(r"\b(?:hoaks|hoax|berita\s+bohong|kabar\s+dusta|kabar\s+burung|fitnah\s+keji|pesan\s+berantai|klaim\s+palsu)\b", re.I),
    re.compile(r"\b(?:sebar(?:kan)?\s+sebelum\s+dihapus|info\s+a1\b|jangan\s+berhenti\s+di\s+(?:kamu|anda)|bagikan\s+ke\s+(?:semua\s+)?grup|viralkan\s+sekarang|jangan\s+mau\s+dibohongi\s+media)\b", re.I),
    
    # Konspirasi & Politik / Pemilu Palsu
    re.compile(r"\b(?:jual\s+pulau|jual\s+negara|dikuasai\s+china|tentara\s+china|pulau\s+jawa\s+dijual|indonesia\s+dijual|aset\s+negara\s+dijual)\b", re.I),
    re.compile(r"\b(?:ijazah\s+(?:jokowi\s+)?palsu|jokowi\s+ijazah\s+palsu|presiden\s+bohong|janji\s+palsu)\b", re.I),
    re.compile(r"\b(?:kas\s+negara\s+kosong|uang\s+negara\s+ludes|dirampok\s+pejabat|bank\s+bangkrut\s+total|uang\s+nasabah\s+disita)\b", re.I),
    re.compile(r"\b(?:server\s+kpu\s+diretas|suara\s+dialihkan|kpu\s+curang|pemilu\s+curang)\b", re.I),
    re.compile(r"\b(?:korupsi\s+.*triliunan|korupsi\s+dana\s+bansos|kabur\s+ke\s+luar\s+negeri|dituduh\s+selingkuh|video\s+syur\s+pejabat)\b", re.I),
    re.compile(r"\b(?:menteri|pejabat|mentan|menkeu|menkopolhukam|menag)\b.*\b(?:mundur|mengundurkan\s+diri|kabur|ditangkap\s+kpk\s+palsu)\b", re.I),
    re.compile(r"\b(?:bumi\s+(?:itu\s+)?datar|konspirasi\s+elit\s+global|pendaratan\s+bulan\s+sandiwara|chemtrail)\b", re.I),

    # Pseudosains & Medis Palsu
    re.compile(r"\b(?:racun\s+kimia|racun\s+sianida|beras\s+plastik|telur\s+(?:palsu|plastik)|garam\s+kaca)\b", re.I),
    re.compile(r"\bvaksina?.*\b(?:beracun|microchip|chip|5g|autis|kematian|mandul|lumpuh|darah\s+babi)\b", re.I),
    re.compile(r"\b(?:air\s+rebusan|ramuan|bawang\s+putih|daun\s+sirsak|kelapa\s+hijau|jeruk\s+nipis|garam\s+kasar)\b.*\b(?:sembuh|bunuh|kanker|stroke|diabetes|tumor|covid|corona|racun)\b", re.I),
    re.compile(r"\b(?:sembuh(?:kan)?|matikan|bunuh|cegah)\s+(?:semua\s+)?(?:kanker|tumor|stroke|covid|corona|virus|diabetes|hiv|aids)\b", re.I),
    re.compile(r"\b(?:susu\s+beruang|air\s+kelapa)\b.*\b(?:netralisir|racun|paru-paru)\b", re.I),

    # Bansos, Hadiah & Undian Phishing
    re.compile(r"\b(?:bagi-?bagi|bagikan|pembagian)\s+(?:bansos|blt|subsidi|saldo|uang|sepeda|mobil|pulsa|kuota|hadiah)\b", re.I),
    re.compile(r"\b(?:klaim|daftar|ambil)\s+(?:saldo|bantuan|bansos|kuota|dana\s+kaget|hadiah|voucher)\b", re.I),
    re.compile(r"\b(?:uang\s+koin|koin\s+seribu|uang\s+kuno)\b.*\b(?:kelapa\s+sawit|melati|ratusan\s+juta|dibeli)\b", re.I),
    re.compile(r"\b(?:uang\s+(?:pecahan\s+)?(?:100|50|75)\s*ribu\s+ditarik\s+bi|(?:ditarik|uang ditarik)\s+bi)\b", re.I),
    re.compile(r"\b(?:penerimaan|pengangkatan)\s+(?:cpns|pns|bumn)\b.*\b(?:tanpa\s+tes)\b", re.I),
    re.compile(r"\b(?:t\.me|bit\.ly|s\.id|wa\.me)/[a-zA-Z0-9_-]+\b", re.I),

    # Bencana & Kepanikan Palsu
    re.compile(r"\b(?:megathrust|tsunami|gempa\s+dahsyat|topan\s+tornado)\b.*\b(?:malam\s+ini|dini\s+hari|jam\s+\d|tenggelamkan|keluar\s+rumah|sapu\s+bersih)\b", re.I),
    re.compile(r"\b(?:gempa\s+(?:10|11|12|9\.\d)\s*(?:sr|skala\s+richter|magnitudo))\b", re.I),
    re.compile(r"\b(?:penculikan\s+anak|diculik)\b.*\b(?:mobil\s+(?:box\s+)?putih|organ\s+tubuh|badut)\b", re.I),
]

FACT_INDICATOR_PATTERNS = [
    # Pejabat & Aksi Pembangunan Jurnalistik
    re.compile(r"\b(?:presiden|menteri|kementerian|pemprov|pemkot|pemkab|gubernur|wali\s+kota|bupati)\b.*\b(?:meresmikan|meninjau|mengalokasikan|meluncurkan|menandatangani|melantik|membuka)\b", re.I),
    re.compile(r"\b(?:meresmikan|peresmian)\s+(?:jalan\s+tol|jembatan|bendungan|gedung|fasilitas|rumah\s+sakit|pelabuhan|bandara|puskesmas)\b", re.I),
    re.compile(r"\b(?:kementerian|kemenkes|kemendikbud|kemenkeu|kemlu|kemenag|pupr|pemprov|pemkot|pemkab|dinas)\b.*\b(?:meresmikan|membangun|menyelesaikan|meninjau|mengumumkan|mengalokasikan|meluncurkan)\b", re.I),
    
    # Penegakan Hukum & Kamtibmas
    re.compile(r"\b(?:polda|polrestabes|polres|polri|satuan\s+narkoba|tim\s+resmob|aparat\s+kepolisian)\b.*\b(?:menangkap|membekuk|mengamankan|meringkus|menggagalkan|menyita\s+barang\s+bukti|menertibkan)\b", re.I),
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
    # Kekerasan, Penyerangan, Pembakaran & Anarki
    re.compile(r"\b(?:serbu|bakar|hancurkan|robohkan|rusak|jarah|bom|geruduk)\s+(?:dan\s+bakar|kantor|gedung|markas|posko|toko|rumah|fasilitas|polsek|polres|ibadah|masjid|gereja)\b", re.I),
    re.compile(r"\b(?:bakar\s+rumah\s+ibadah|bakar\s+posko|bakar\s+kantor|bakar\s+gedung|bakar\s+fasilitas)\b", re.I),
    re.compile(r"\b(?:bunuh|bantai|habisi|mati\s+kau|tumpahkan\s+darah|perang\s+terbuka|perang\s+suku|angkat\s+senjata|celurit|sajam)\b", re.I),
    
    # Umpatan, Makian & Hinaan Kasar
    re.compile(r"\b(?:bajingan|bangsat|keparat|biadab|anjing|babi|kontol|bejat|tolol|goblok|laknat|iblis|setan)\b", re.I),
    
    # Ujaran Kebencian / SARA
    re.compile(r"\b(?:antek\s+(?:pki|aseng|asing|komunis|yahudi|zionis)|pki\s+bangkit|komunis\s+bangkit)\b", re.I),
    re.compile(r"\b(?:usir|ganyang)\s+.*(?:cina|chinese|etnis|pendatang|asing|kaum|mereka)\b", re.I),
    re.compile(r"\b(?:kafir|sesat|munafik|syiah\s+sesat|wahabi\s+teroris)\b", re.I),
    
    # Provokasi Makar, Anarki & Penindasan
    re.compile(r"\b(?:rezim\s+(?:bangsat|zalim|dholim|iblis|bobrok|firaun|penindas|korup)|pemerintah\s+(?:zalim|dholim|korup|bejat|tidak\s+becus)|aparat\s+(?:keparat|biadab|anjing))\b", re.I),
    re.compile(r"\b(?:tidak\s+becus|gulingkan|turunkan\s+paksa|kudeta|revolusi\s+rakyat|amuk\s+massa|sweeping|penindas\s+rakyat)\b", re.I),
    re.compile(r"\b(?:gantung\s+(?:semua\s+)?pejabat|hukum\s+mati\s+(?:presiden|menteri|pejabat))\b", re.I),
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
    Menggunakan pendekatan Hybrid Threat Intelligence:
    1. Priority 1: Ujaran kebencian / provokasi / kekerasan (NEGATIF)
    2. Priority 2: Disinformasi / klaim bohong / konspirasi (HOAKS)
    3. Priority 3: Rilis resmi / berita 5W1H (FAKTA)
    4. Priority 4: Statistical SGDClassifier + Sentiment Polarity Filter
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
    neg_match_count = sum(1 for pat in NEGATIVE_INDICATOR_PATTERNS if pat.search(raw_lower))
    hoax_match_count = sum(1 for pat in HOAX_INDICATOR_PATTERNS if pat.search(raw_lower))
    fact_match_count = sum(1 for pat in FACT_INDICATOR_PATTERNS if pat.search(raw_lower))

    # Cek apakah ini debunk resmi oleh media/lembaga CekFakta (misal: "Klarifikasi Mafindo:")
    is_official_debunk = any(w in raw_lower for w in ["klarifikasi", "cek fakta", "cekfakta", "turnbackhoax"])

    # ==============================================================
    # HIERARKI PENILAIAN ANCAMAN (THREAT INTELLIGENCE PRIORITY)
    # ==============================================================
    if neg_match_count > 0:
        # PRIORITAS TERTINGGI: Ancaman Provokasi / Kebencian / Anarki
        base_prob = min(0.92 + (0.02 * neg_match_count), 0.99)
        prob_dict["negatif"] = base_prob
        prob_dict["hoaks"] = (1.0 - base_prob) * 0.6
        prob_dict["fakta"] = (1.0 - base_prob) * 0.4

    elif hoax_match_count > 0:
        # PRIORITAS KEDUA: Disinformasi & Kabar Bohong
        if is_official_debunk:
            prob_dict["fakta"] = 0.94
            prob_dict["hoaks"] = 0.04
            prob_dict["negatif"] = 0.02
        else:
            base_prob = min(0.92 + (0.02 * hoax_match_count), 0.99)
            prob_dict["hoaks"] = base_prob
            prob_dict["fakta"] = (1.0 - base_prob) * 0.4
            prob_dict["negatif"] = (1.0 - base_prob) * 0.6

    elif fact_match_count > 0:
        # PRIORITAS KETIGA: Berita Resmi / Jurnalistik Terverifikasi
        base_prob = min(0.92 + (0.02 * fact_match_count), 0.99)
        prob_dict["fakta"] = base_prob
        prob_dict["hoaks"] = (1.0 - base_prob) * 0.5
        prob_dict["negatif"] = (1.0 - base_prob) * 0.5

    else:
        # PRIORITAS KEEMPAT: Teks Umum Tanpa Keyword Spesifik
        # Analisis sentimen negatif umum untuk mencegah kalimat provokasi terselubung jadi "fakta"
        negative_words = re.findall(
            r"\b(?:korup|korupsi|bejat|rusak|rusuh|curang|bobrok|zalim|dholim|penipu|bohong|dusta|palsu|ribut|kacau|bentrok|hajar|serang|lawan|demo|kecewa|gagal|hancur|bahaya|mati|pembunuhan|teror|benci|ancam|biadab|anjing|bajingan|antek)\b",
            raw_lower
        )
        if len(negative_words) > 0:
            # Tekan probabilitas fakta jika terdapat kata-kata beracun/kritik keras
            prob_dict["fakta"] = max(0.05, prob_dict["fakta"] - 0.45)
            prob_dict["negatif"] += 0.35
            prob_dict["hoaks"] += 0.10

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
