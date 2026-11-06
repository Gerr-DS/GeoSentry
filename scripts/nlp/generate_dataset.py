"""
GeoSentry - Generator Dataset Pelatihan NLP Komprehensif (Zero-Token ML)
Menghasilkan korpus data/train.csv dengan distribusi kelas seimbang:
1. FAKTA: Berita resmi, jurnalistik 5W1H, BMKG, kepolisian, pemerintahan, ekonomi, olahraga.
2. HOAKS: Disinformasi terverifikasi Mafindo/TurnBackHoax, klaim pseudosains, phishing/bansos,
          konspirasi politik, pesan berantai WA, kepanikan gempa/bencana palsu.
3. NEGATIF: Provokasi kekerasan, amuk massa, sweeping bersenjata, ujaran kebencian ekstrem.
"""

import os
import csv
import random

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.path.join(BASE_DIR, "data")
TRAIN_CSV_PATH = os.path.join(DATA_DIR, "train.csv")

# ==============================================================
# 1. KORPUS DASAR FAKTA (Berita Jurnalistik & Rilis Resmi)
# ==============================================================
BASE_FACTS = [
    # Infrastruktur & Transportasi
    "Presiden Joko Widodo didampingi Menteri PUPR meresmikan jalan tol trans sepanjang 45 kilometer guna mempercepat konektivitas logistik daerah.",
    "Kementerian Pekerjaan Umum menyelesaikan pembangunan bendungan baru untuk mengairi ribuan hektare sawah warga dan mengendalikan banjir.",
    "Pemerintah Provinsi mengalokasikan anggaran perbaikan jembatan gantung dan jalan penghubung antar desa guna memperlancar mobilitas warga.",
    "Wali Kota meninjau kesiapan rumah pompa dan sistem drainase kota dalam menghadapi potensi curah hujan tinggi akhir pekan ini.",
    "Dinas Perhubungan mengumumkan rekayasa lalu lintas satu arah sementara di jalur protokol selama pelaksanaan kegiatan maraton berlangsung.",
    "PT Kereta Api Indonesia mengoperasikan rangkaian kereta tambahan untuk melayani lonjakan penumpang pada musim liburan akhir tahun.",
    "Pemerintah Kota meluncurkan armada bus listrik ramah lingkungan untuk mendukung integrasi transportasi umum massal.",
    "Pembangunan terminal pelabuhan peti kemas baru telah mencapai sembilan puluh persen dan ditargetkan beroperasi penuh bulan depan.",

    # Kepolisian, Kejaksaan & Penegakan Hukum
    "Aparat Polrestabes berhasil membekuk komplotan spesialis pencurian kendaraan bermotor yang kerap beraksi di wilayah permukiman warga.",
    "Satuan Reserse Narkoba Polda menyita puluhan kilogram barang bukti narkotika jenis sabu dari jaringan penyelundup lintas provinsi.",
    "Kejaksaan Negeri melimpahkan berkas perkara dugaan korupsi pengelolaan dana desa ke Pengadilan Tindak Pidana Korupsi untuk persidangan.",
    "Polda Metro Jaya mengungkap sindikat penipuan daring bermodus lowongan kerja paruh waktu dan mengamankan lima orang tersangka.",
    "Tim gabungan TNI dan Polri meningkatkan patroli pengamanan bersama di wilayah perbatasan guna mencegah kegiatan penyelundupan ilegal.",
    "Polres setempat mengamankan puluhan knalpot tidak standar dalam operasi razia penertiban balap liar demi ketertiban lingkungan.",
    "Petugas bea cukai menggagalkan penyelundupan jutaan batang rokok ilegal tanpa pita cukai di pelabuhan penyeberangan antar pulau.",

    # BMKG, Cuaca & Mitigasi Bencana
    "BMKG merilis informasi gempa bumi tektonik berkekuatan magnitudo 5.1 di laut barat daya dan memastikan tidak berpotensi tsunami.",
    "Badan Penanggulangan Bencana Daerah menyalurkan bantuan logistik darurat, tenda pengungsian, dan air bersih bagi korban banjir bandang.",
    "Berdasarkan prakiraan cuaca stasiun BMKG, wilayah Jabodetabek berpotensi mengalami hujan dengan intensitas ringan hingga sedang sore nanti.",
    "Petugas pos pemantauan gunung api mencatat puluhan guguran lava pijar sejauh seribu meter ke arah barat daya, status tetap waspada.",
    "Tim SAR gabungan berhasil menemukan dan mengevakuasi seluruh awak kapal nelayan yang sempat dilaporkan hilang kontak akibat ombak tinggi.",
    "Stasiun geofisika memaparkan bahwa rentetan gempa susulan berkekuatan rendah merupakan pelepasan energi wajar pasca gempa utama.",

    # Ekonomi, Perbankan & Bisnis
    "Bank Indonesia dalam Rapat Dewan Gubernur memutuskan untuk mempertahankan suku bunga acuan BI-Rate di level 6.00 persen demi stabilitas rupiah.",
    "Badan Pusat Statistik mengumumkan laju pertumbuhan ekonomi nasional pada triwulan ketiga tercatat tumbuh positif sebesar 5.05 persen secara tahunan.",
    "Kementerian Perdagangan bekerja sama dengan dinas terkait menggelar operasi pasar murah beras dan minyak goreng curah untuk menekan inflasi pangan.",
    "Perum Bulog memastikan stok cadangan beras pemerintah di gudang penyimpanan mencukupi kebutuhan masyarakat hingga memasuki awal tahun depan.",
    "Nilai ekspor komoditas perkebunan dan perikanan nasional membukukan kenaikan signifikan pada periode kuartal kedua tahun ini.",
    "Kementerian Keuangan melaporkan kinerja penerimaan pajak negara tetap terjaga positif sesuai dengan target rancangan APBN.",

    # Kesehatan & Pendidikan
    "Kementerian Kesehatan mengimbau seluruh orang tua untuk melengkapi status imunisasi dasar pada balita melalui puskesmas dan posyandu terdekat.",
    "Dinas Kesehatan setempat menggelar program fogging serentak di kawasan permukiman warga guna menekan penularan demam berdarah dengue.",
    "Kemendikbudristek secara resmi mengumumkan jadwal dan mekanisme seleksi nasional penerimaan mahasiswa baru pada perguruan tinggi negeri.",
    "Rumah Sakit Umum Daerah membuka layanan rawat inap baru berkapasitas lima puluh tempat tidur untuk meningkatkan mutu pelayanan kesehatan masyarakat.",
    "Kemenkes mendistribusikan ratusan ribu dosis vaksin influenza dan obat-obatan esensial ke seluruh dinas kesehatan kabupaten kota.",

    # Olahraga & Kebudayaan
    "Tim nasional sepak bola Indonesia sukses meraih kemenangan meyakinkan dengan skor 2-0 pada pertandingan lanjutan babak kualifikasi di Stadion GBK.",
    "Pasangan ganda putra bulutangkis Indonesia melaju ke partai puncak turnamen internasional setelah menundukkan unggulan pertama.",
    "Atlet panahan nasional menyumbang medali emas setelah menorehkan skor tertinggi pada babak final kejuaraan olahraga tingkat Asia.",
    "Pemerintah Daerah menggelar festival kebudayaan dan pawai kesenian tradisional yang disaksikan oleh ribuan warga masyarakat di alun-alun kota."
]

# ==============================================================
# 2. KORPUS DASAR HOAKS (Pseudosains, Phishing, Konspirasi, Panik)
# ==============================================================
BASE_HOAXES = [
    # Phishing, Hadiah & Bansos Palsu
    "Kabar gembira! Presiden membagikan bantuan bansos tunai 5 juta rupiah per keluarga hari ini, segera daftar nomor HP lewat link t.me/bansos-gratis!",
    "Beredar pesan WhatsApp: Bank Indonesia memutihkan seluruh catatan pinjaman online dan bagikan saldo DANA 3 juta lewat tautan berikut!",
    "Kemendikbud bagi-bagi subsidi kuota internet belajar 100GB cuma-cuma untuk semua murid dan guru, klaim kuota sekarang di link bit.ly/kuota-resmi!",
    "Undian resmi Pertamina perayaan hari jadi ke-68 membagikan voucher bensin 1 juta rupiah, klik tautan ini dan teruskan ke lima grup WA sebelum hangus!",
    "Pemberitahuan darurat BPJS Kesehatan: saldo klaim bantuan tunai senilai 4.500.000 rupiah bisa dicairkan sekarang dengan mengisi formulir daring ini!",
    "Hadiah langsung mobil Toyota Alphard dan uang tunai tanpa diundi dari bank nasional, silakan hubungi nomor WhatsApp pribadi pimpinan ini!",
    "Dapatkan saldo gratis 500 ribu langsung masuk dompet digital tanpa syarat apapun, hanya berlaku untuk 1000 pendaftar pertama lewat tautan ini!",
    "Beredar edaran pengangkatan honorer menjadi PNS tanpa tes seleksi dari BKN dengan syarat membayar uang administrasi seragam dua juta rupiah!",

    # Pseudosains, Medis & Urban Legend Bahan Makanan
    "Klaim dokter herbal: rutin meminum air rebusan pare campur garam kasar setiap malam terbukti mematikan sel kanker ganas dan stroke dalam tiga hari!",
    "Awas jangan mau disuntik vaksin penguat terbaru di faskes, ada microchip buatan asing yang ditanam untuk menyadap pikiran dan mengendalikan manusia!",
    "Viral di media sosial: beras plastik sintetis berbahan lem kimia beredar luas di pasar tradisional, bila dimasak teksturnya kenyal merusak lambung!",
    "Hati-hati konsumsi garam dapur kemasan impor karena mengandung serpihan kaca tajam dan serbuk beracun sengaja disebar sindikat gelap!",
    "Kabar berantai mengklaim minum susu beruang bisa menetralkan semua racun mematikan, virus flu burung, dan membersihkan paru-paru seketika!",
    "Jangan minum air es setelah makan gorengan karena darah langsung membeku di pembuluh jantung dan menyebabkan kematian mendadak!",
    "Ditemukan ulat beracun mematikan di pohon mangga, bila tersengat kulit langsung melepuh dan korban bisa meninggal dalam waktu empat jam!",
    "Klaim air rebusan serai campur jeruk nipis dan kelapa hijau terbukti mujarab membunuh seluruh virus dalam hitungan jam tanpa obat dokter!",
    "Awas meteran listrik token PLN di perumahan dipasangi chip penyadap rahasia intelijen asing untuk merekam pembicaraan keluarga di rumah!",

    # Kepanikan Gempa, Tsunami & Bencana Palsu
    "Peringatan rahasia BMKG bocor: gempa megathrust dahsyat magnitudo 9.8 akan menenggelamkan pulau Jawa malam ini jam 1 dini hari, segera keluar rumah!",
    "Beredar rekaman suara: gelombang tsunami raksasa setinggi 30 meter diprediksi akan menyapu bersih garis pantai malam ini, sebarkan ke keluarga!",
    "Info darurat orang dalam: letusan dahsyat gunung berapi memuntahkan gas beracun mematikan ke udara, seluruh warga wajib mengungsi detik ini juga!",
    "Pesan suara viral mengabarkan badai topan tornado raksasa sedang meluncur ke arah ibu kota malam ini, tolong sebarkan sebelum terlambat!",

    # Isu Politik, Keuangan Palsu & Konspirasi
    "Bongkar rekaman rahasia: server KPU telah diretas dan suara pemilihan di seluruh wilayah telah dialihkan 100 persen tanpa meninggalkan jejak!",
    "Informasi valid dari orang dalam istana: menteri keuangan dan menteri pertahanan serentak mengundurkan diri dari jabatan kabinet presiden!",
    "Foto editan viral: kantor dewan perwakilan roboh dibakar massa demonstran, padahal kenyataannya gedung tersebut masih utuh tanpa kerusakan!",
    "Waspada sindikat penculikan anak berkeliaran naik mobil box putih di sekitar lingkungan sekolah dasar, organ tubuh korban dijual ke pasar gelap!",
    "Mulai besok Bank Indonesia menarik uang pecahan seratus ribu dan lima puluh ribu rupiah dari peredaran sehingga tidak berlaku lagi!",
    "Uang koin logam seribu rupiah gambar kelapa sawit dibeli oleh bank sentral seharga seratus juta rupiah per keping secara tunai!",
    "Beredar kabar ribuan tentara asing bersenjata mendarat diam-diam di bandara perintis pulau terpencil untuk mengambil alih aset negara!",
    "Bumi itu terbukti datar dan pendaratan manusia di luar angkasa adalah sandiwara konspirasi elit global demi meraup keuntungan dana!"
]

# ==============================================================
# 3. KORPUS DASAR NEGATIF (Ujaran Provokasi, Kerusuhan, Ekstrem)
# ==============================================================
BASE_NEGATIVES = [
    "Dasar aparat keparat pelindung penjahat! Ayo kawan-kawan serbu dan bakar kantor markas polisi sekarang juga, jangan beri mereka ampun!",
    "Rezim bangsat pengkhianat rakyat! Saatnya kita angkat senjata, bawa bom molotov dan gulingkan penguasa zalim ini sampai berdarah-darah!",
    "Usir semua etnis pendatang parasit itu dari kampung kita! Bakar rumah dan hancurkan toko-toko milik mereka sampai rata dengan tanah!",
    "Habisi dan bantai kaum minoritas itu tanpa sisa! Darah mereka halal ditumpahkan di tengah jalanan, jangan pernah ada kata damai bagi mereka!",
    "Malam ini sweeping seluruh jalur protokol, bawa celurit dan sajam, habisi siapa saja kelompok musuh yang berani lewat melintas!",
    "Kepung dan bakar kantor bupati sekarang juga! Hancurkan semua fasilitas umum dan bikin kota ini lumpuh total oleh amuk massa malam ini!",
    "Biadab! Kaum munafik pengkhianat harus kita musnahkan dari muka bumi, bersiaplah untuk perang terbuka antar kubu sampai titik penghabisan!",
    "Mampus kalian para penjilat kekuasaan! Rumah dan kendaraan keluarga kalian sudah ditandai, kami siap ratakan dengan tanah malam nanti!",
    "Boikot total dan jarah semua gudang logistik milik perusahaan itu, robohkan pagar dan hajar pengawalnya sampai babak belur!",
    "Gantung semua pejabat korup di tiang listrik jalanan tanpa sidang, biar jadi tontonan dan peringatan bagi antek-antek asing penguasa!",
    "Ayo kawan buruh turun ke jalan dengan balok kayu dan batu, kita robohkan gerbang gedung dewan dan bikin rusuh sampai tuntutan dipenuhi!"
]

# Variasi Wilayah Indonesia
KOTA_LIST = [
    "Jakarta", "Surabaya", "Bandung", "Medan", "Semarang", "Makassar", "Palembang",
    "Yogyakarta", "Solo", "Denpasar", "Balikpapan", "Samarinda", "Banda Aceh",
    "Pekanbaru", "Batam", "Padang", "Pontianak", "Banjarmasin", "Manado", "Jayapura",
    "Malang", "Bogor", "Bekasi", "Depok", "Tangerang", "Cirebon", "Kupang", "Mataram",
    "Ambon", "Ternate", "Kendari", "Palu", "Gorontalo", "Mamuju", "Tarakan"
]

def generate_rich_dataset():
    os.makedirs(DATA_DIR, exist_ok=True)
    rows = []

    # 1. Masukkan sampel inti berkualitas tinggi
    for s in BASE_FACTS:
        rows.append({"teks": s, "label": "fakta"})
    for s in BASE_HOAXES:
        rows.append({"teks": s, "label": "hoaks"})
    for s in BASE_NEGATIVES:
        rows.append({"teks": s, "label": "negatif"})

    # 2. Augmentasi FAKTA dengan variasi nama kota dan kata pengantar jurnalistik
    fact_prefixes = [
        "Laporan terkini: ", "Siaran pers resmi: ", "Keterangan pers Humas: ",
        "Warta daerah: ", "Berdasarkan rilis resmi pemerintah, ",
        "Pihak berwenang mengonfirmasi bahwa ", "Hasil peninjauan lapangan menunjukkan ",
        "Dalam konferensi pers hari ini disampaikan bahwa ", ""
    ]
    for _ in range(500):
        base = random.choice(BASE_FACTS)
        prefix = random.choice(fact_prefixes)
        kota = random.choice(KOTA_LIST)
        augmented = f"{prefix}{base} Kegiatan tersebut berlangsung tertib di wilayah {kota}."
        rows.append({"teks": augmented, "label": "fakta"})

    # 3. Augmentasi HOAKS dengan trigger kata sensasional dan desakan forward WA
    hoax_prefixes = [
        "Awas viral! ", "Tolong sebarkan sebelum dihapus Kominfo! ", "Info A1 orang dalam: ",
        "Jangan mau dibohongi media mainstream! ", "Peringatan darurat nasional: ",
        "Kabar heboh menggemparkan: ", "Wajib disebarkan ke semua grup keluarga! ",
        "Astaghfirullah viral di WhatsApp: ", "Bongkar fakta mengejutkan: ", ""
    ]
    hoax_suffixes = [
        " Sebarkan seluas-luasnya jangan berhenti di kamu!",
        " Segera klaim sebelum kuota habis!",
        " Viralkan biar seluruh rakyat tahu kebenarannya!",
        " Waspada jangan sampai jadi korban berikutnya!",
        " Forward ke grup WA sekarang juga!"
    ]
    for _ in range(500):
        base = random.choice(BASE_HOAXES)
        prefix = random.choice(hoax_prefixes)
        suffix = random.choice(hoax_suffixes)
        augmented = f"{prefix}{base}{suffix}"
        rows.append({"teks": augmented, "label": "hoaks"})

    # 4. Augmentasi NEGATIF dengan seruan provokasi dan amuk massa
    neg_prefixes = [
        "Aksi balas dendam: ", "Seruan perang terbuka! ", "Jangan cuma diam saja: ",
        "Massa siap geruduk: ", "Waspada provokasi berdarah: ", "Ayo bersatu gulingkan: ", ""
    ]
    neg_suffixes = [
        " Jangan biarkan mereka lolos hidup-hidup!",
        " Buat mereka merasakan kemarahan rakyat!",
        " Bakar dan ratakan dengan tanah!",
        " Siapkan senjata tajam kalian malam ini!",
        " Kita buat kota ini lumpuh total!"
    ]
    for _ in range(500):
        base = random.choice(BASE_NEGATIVES)
        prefix = random.choice(neg_prefixes)
        suffix = random.choice(neg_suffixes)
        kota = random.choice(KOTA_LIST)
        augmented = f"{prefix}{base} Titik kumpul massa dipusatkan di {kota}.{suffix}"
        rows.append({"teks": augmented, "label": "negatif"})

    # Acak dataset secara deterministik
    random.seed(42)
    random.shuffle(rows)

    with open(TRAIN_CSV_PATH, mode="w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["teks", "label"])
        writer.writeheader()
        writer.writerows(rows)

    print(f"[✓] Berhasil membuat dataset pelatihan komprehensif: {TRAIN_CSV_PATH}")
    print(f"    Total baris data: {len(rows)}")
    labels_count = {}
    for r in rows:
        labels_count[r["label"]] = labels_count.get(r["label"], 0) + 1
    print(f"    Distribusi kelas: {labels_count}")

if __name__ == "__main__":
    generate_rich_dataset()
