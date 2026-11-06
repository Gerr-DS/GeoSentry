"""
GeoSentry - Modul Pelatihan Incremental Machine Learning (Zero-Token ML)
Melatih model dasar SGDClassifier menggunakan HashingVectorizer.
Mendukung Online Learning (partial_fit) untuk feedback loop analis secara langsung.
"""

import os
import re
import sys
import joblib
import pandas as pd
from sklearn.feature_extraction.text import HashingVectorizer
from sklearn.linear_model import SGDClassifier
from sklearn.metrics import classification_report, accuracy_score
from sklearn.model_selection import train_test_split
from Sastrawi.StopWordRemover.StopWordRemoverFactory import StopWordRemoverFactory

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.path.join(BASE_DIR, "data")
MODELS_DIR = os.path.join(BASE_DIR, "models")
TRAIN_CSV = os.path.join(DATA_DIR, "train.csv")
MODEL_PKL = os.path.join(MODELS_DIR, "sgd_model.pkl")

# Inisialisasi Stopword Remover Sastrawi
stop_factory = StopWordRemoverFactory()
stopword_remover = stop_factory.create_stop_word_remover()

CLASSES = ["fakta", "hoaks", "negatif"]


def preprocess_text(text: str) -> str:
    """
    Pembersihan teks Bahasa Indonesia:
    1. Lowercase
    2. Hapus URL, hashtag, mention
    3. Hapus karakter non-alfabet
    4. Sastrawi Stopword removal
    """
    if not isinstance(text, str):
        return ""
    
    text = text.lower()
    text = re.sub(r"https?://\S+|www\.\S+", " ", text)
    text = re.sub(r"[@#]\w+", " ", text)
    text = re.sub(r"[^a-z\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = stopword_remover.remove(text)
    return text


def train_model():
    print(f"[+] Memuat data latihan dari {TRAIN_CSV}...")
    if not os.path.exists(TRAIN_CSV):
        raise FileNotFoundError(f"File {TRAIN_CSV} tidak ditemukan! Jalankan generate_dataset.py terlebih dahulu.")

    df = pd.read_csv(TRAIN_CSV)
    df = df.dropna(subset=["teks", "label"])
    
    print(f"[+] Total data: {len(df)} baris. Distribusi kelas:")
    print(df["label"].value_counts())

    # Preprocessing teks
    print("[+] Melakukan prapemrosesan teks (Sastrawi + normalisasi)...")
    cleaned_texts = [preprocess_text(t) for t in df["teks"]]

    # Split train-test (80:20)
    X_train_raw, X_test_raw, y_train, y_test = train_test_split(
        cleaned_texts, df["label"], test_size=0.2, random_state=42, stratify=df["label"]
    )

    # 1. Stateless HashingVectorizer (2^18 fitur, unigram + bigram)
    print("[+] Menginisialisasi HashingVectorizer (n_features=2^18, ngrams=(1,2))...")
    vectorizer = HashingVectorizer(
        n_features=2**18,
        alternate_sign=False,
        ngram_range=(1, 2),
        norm="l2"
    )

    X_train = vectorizer.transform(X_train_raw)
    X_test = vectorizer.transform(X_test_raw)

    # 2. Incremental SGDClassifier dengan loss='log_loss' (mendukung predict_proba & partial_fit)
    print("[+] Melatih SGDClassifier (loss='log_loss', penalty='l2', alpha=1e-5)...")
    clf = SGDClassifier(
        loss="log_loss",
        penalty="l2",
        alpha=1e-5,
        max_iter=1000,
        random_state=42
    )

    # Latih model secara konvergen dengan fit (bisa dilanjutkan dengan partial_fit saat online learning)
    clf.fit(X_train, y_train)

    # Evaluasi pada data uji
    y_pred = clf.predict(X_test)
    acc = accuracy_score(y_test, y_pred)
    print(f"\n[✓] Akurasi pada data uji: {acc * 100:.2f}%")
    print("\n--- Laporan Klasifikasi ---")
    print(classification_report(y_test, y_pred, target_names=CLASSES))

    # Simpan model dan metadata
    os.makedirs(MODELS_DIR, exist_ok=True)
    bundle = {
        "vectorizer": vectorizer,
        "classifier": clf,
        "classes": CLASSES,
        "version": "2.0.0"
    }
    joblib.dump(bundle, MODEL_PKL)
    print(f"[✓] Model berhasil disimpan ke {MODEL_PKL}")
    return acc


if __name__ == "__main__":
    train_model()
