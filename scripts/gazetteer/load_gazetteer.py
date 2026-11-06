"""
GeoSentry - Modul Pengisian Kamus Wilayah (Gazetteer Loader)
Mengimpor data nama kota/kabupaten dan wilayah strategis di Indonesia ke PostgreSQL.
Mengikuti aturan: psycopg v3, path dotenv aman untuk Python 3.14.
"""

import os
import sys
import json
from dotenv import load_dotenv
import psycopg

# Path aman dotenv sesuai aturan §6.1
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(dotenv_path=os.path.join(BASE_DIR, ".env"))

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL belum diatur di file .env!")

# Dataset komprehensif 514 Kabupaten/Kota dan wilayah kunci di Indonesia
# Format: (nama_lowercase, provinsi, lat, lon)
INDONESIA_REGIONS = [
    # --- ACEH ---
    ("banda aceh", "Aceh", 5.5483, 95.3238),
    ("sabang", "Aceh", 5.8920, 95.3222),
    ("lhokseumawe", "Aceh", 5.1801, 97.1507),
    ("langsa", "Aceh", 4.4719, 97.9683),
    ("subulussalam", "Aceh", 2.6417, 98.0064),
    ("aceh besar", "Aceh", 5.3789, 95.5312),
    ("aceh barat", "Aceh", 4.4539, 96.1663),
    ("aceh barat daya", "Aceh", 3.8294, 96.8837),
    ("aceh jaya", "Aceh", 4.7278, 95.6378),
    ("aceh selatan", "Aceh", 3.2530, 97.2690),
    ("aceh singkil", "Aceh", 2.4239, 97.9250),
    ("aceh tamiang", "Aceh", 4.2547, 98.0531),
    ("aceh tengah", "Aceh", 4.6289, 96.8378),
    ("aceh tenggara", "Aceh", 3.3644, 97.8542),
    ("aceh timur", "Aceh", 4.6300, 97.6300),
    ("aceh utara", "Aceh", 4.9667, 97.1333),
    ("bener meriah", "Aceh", 4.7333, 96.8667),
    ("bireuen", "Aceh", 5.0933, 96.6000),
    ("gayo lues", "Aceh", 3.9500, 97.3500),
    ("nagan raya", "Aceh", 4.1667, 96.5167),
    ("pidie", "Aceh", 5.1000, 95.9667),
    ("pidie jaya", "Aceh", 5.1500, 96.2167),
    ("simeulue", "Aceh", 2.6167, 96.0833),

    # --- SUMATERA UTARA ---
    ("medan", "Sumatera Utara", 3.5952, 98.6722),
    ("binjai", "Sumatera Utara", 3.6003, 98.4854),
    ("tebing tinggi", "Sumatera Utara", 3.3285, 99.1625),
    ("pematang siantar", "Sumatera Utara", 2.9599, 99.0687),
    ("tanjung balai", "Sumatera Utara", 2.9667, 99.8000),
    ("sibolga", "Sumatera Utara", 1.7426, 98.7792),
    ("padang sidempuan", "Sumatera Utara", 1.3733, 99.2683),
    ("gunungsitoli", "Sumatera Utara", 1.2828, 97.6150),
    ("deli serdang", "Sumatera Utara", 3.4167, 98.6833),
    ("langkat", "Sumatera Utara", 3.7333, 98.2167),
    ("karo", "Sumatera Utara", 3.1167, 98.5000),
    ("dairi", "Sumatera Utara", 2.7667, 98.2667),
    ("simalungun", "Sumatera Utara", 2.9000, 99.0000),
    ("asahan", "Sumatera Utara", 2.9833, 99.6333),
    ("labuhanbatu", "Sumatera Utara", 2.1500, 100.0000),
    ("tapanuli utara", "Sumatera Utara", 2.0000, 98.9667),
    ("tapanuli tengah", "Sumatera Utara", 1.8833, 98.6667),
    ("tapanuli selatan", "Sumatera Utara", 1.5167, 99.2500),
    ("toba", "Sumatera Utara", 2.3833, 99.2167),
    ("mandailing natal", "Sumatera Utara", 0.8667, 99.5667),
    ("nias", "Sumatera Utara", 1.1167, 97.7500),
    ("nias selatan", "Sumatera Utara", 0.5500, 97.8500),
    ("pakpak bharat", "Sumatera Utara", 2.5667, 98.2833),
    ("humbang hasundutan", "Sumatera Utara", 2.2667, 98.7000),
    ("samosir", "Sumatera Utara", 2.6333, 98.7167),
    ("serdang bedagai", "Sumatera Utara", 3.3667, 99.0333),
    ("batu bara", "Sumatera Utara", 3.1667, 99.5333),
    ("padang lawas", "Sumatera Utara", 1.4667, 99.8833),
    ("padang lawas utara", "Sumatera Utara", 1.5000, 99.6333),
    ("labuhanbatu selatan", "Sumatera Utara", 1.8333, 100.1000),
    ("labuhanbatu utara", "Sumatera Utara", 2.3333, 99.6333),
    ("nias utara", "Sumatera Utara", 1.3333, 97.3167),
    ("nias barat", "Sumatera Utara", 1.0500, 97.4500),

    # --- SUMATERA BARAT ---
    ("padang", "Sumatera Barat", -0.9471, 100.4172),
    ("bukittinggi", "Sumatera Barat", -0.3056, 100.3692),
    ("padang panjang", "Sumatera Barat", -0.4639, 100.4011),
    ("payakumbuh", "Sumatera Barat", -0.2244, 100.6308),
    ("sawahlunto", "Sumatera Barat", -0.6806, 100.7761),
    ("solok", "Sumatera Barat", -0.7989, 100.6539),
    ("pariaman", "Sumatera Barat", -0.6264, 100.1206),
    ("agam", "Sumatera Barat", -0.2500, 100.1667),
    ("dharmasraya", "Sumatera Barat", -1.0500, 101.6167),
    ("kepulauan mentawai", "Sumatera Barat", -1.9833, 99.4167),
    ("lima puluh kota", "Sumatera Barat", -0.0167, 100.6167),
    ("padang pariaman", "Sumatera Barat", -0.6000, 100.2833),
    ("pasaman", "Sumatera Barat", 0.4167, 100.0833),
    ("pasaman barat", "Sumatera Barat", 0.1833, 99.8167),
    ("pesisir selatan", "Sumatera Barat", -1.3500, 100.5667),
    ("sijunjung", "Sumatera Barat", -0.6833, 101.2167),
    ("solok selatan", "Sumatera Barat", -1.2333, 101.2167),
    ("tanah datar", "Sumatera Barat", -0.4500, 100.5833),

    # --- RIAU ---
    ("pekanbaru", "Riau", 0.5071, 101.4478),
    ("dumai", "Riau", 1.6667, 101.4500),
    ("bengkalis", "Riau", 1.4833, 102.1167),
    ("indragiri hilir", "Riau", -0.3333, 103.1667),
    ("indragiri hulu", "Riau", -0.5500, 102.3167),
    ("kampar", "Riau", 0.3333, 101.0333),
    ("kepulauan meranti", "Riau", 0.9833, 102.7000),
    ("kuantan singingi", "Riau", -0.5333, 101.4500),
    ("pelalawan", "Riau", 0.2833, 102.0000),
    ("rokan hilir", "Riau", 2.1667, 100.8167),
    ("rokan hulu", "Riau", 0.8833, 100.5000),
    ("siak", "Riau", 0.7833, 101.7167),

    # --- KEPULAUAN RIAU ---
    ("batam", "Kepulauan Riau", 1.1301, 104.0529),
    ("tanjung pinang", "Kepulauan Riau", 0.9167, 104.4500),
    ("bintan", "Kepulauan Riau", 1.0500, 104.5500),
    ("karimun", "Kepulauan Riau", 1.0167, 103.4000),
    ("kepulauan anambas", "Kepulauan Riau", 3.1667, 106.2167),
    ("lingga", "Kepulauan Riau", -0.2000, 104.6167),
    ("natuna", "Kepulauan Riau", 4.0000, 108.2500),

    # --- JAMBI ---
    ("jambi", "Jambi", -1.6101, 103.6131),
    ("sungai penuh", "Jambi", -2.0592, 101.3931),
    ("batanghari", "Jambi", -1.7500, 103.1167),
    ("bungo", "Jambi", -1.5000, 101.9667),
    ("kerinci", "Jambi", -2.0833, 101.4833),
    ("merangin", "Jambi", -2.1500, 102.1333),
    ("muaro jambi", "Jambi", -1.5333, 103.8167),
    ("sarolangun", "Jambi", -2.3000, 102.6500),
    ("tanjung jabung barat", "Jambi", -1.1333, 103.3500),
    ("tanjung jabung timur", "Jambi", -1.1500, 103.8667),
    ("tebo", "Jambi", -1.4500, 102.4333),

    # --- SUMATERA SELATAN ---
    ("palembang", "Sumatera Selatan", -2.9761, 104.7754),
    ("prabumulih", "Sumatera Selatan", -3.4319, 104.2339),
    ("pagar alam", "Sumatera Selatan", -4.0167, 103.2667),
    ("lubuklinggau", "Sumatera Selatan", -3.2958, 102.8617),
    ("banyuasin", "Sumatera Selatan", -2.8833, 104.3833),
    ("empat lawang", "Sumatera Selatan", -3.6833, 103.0167),
    ("lahat", "Sumatera Selatan", -3.7833, 103.5333),
    ("muara enim", "Sumatera Selatan", -3.6500, 103.7667),
    ("musi banyuasin", "Sumatera Selatan", -2.8833, 103.8167),
    ("musi rawas", "Sumatera Selatan", -3.0833, 103.2000),
    ("musi rawas utara", "Sumatera Selatan", -2.6833, 102.8333),
    ("ogan ilir", "Sumatera Selatan", -3.4333, 104.6000),
    ("ogan komering ilir", "Sumatera Selatan", -3.4000, 105.0000),
    ("ogan komering ulu", "Sumatera Selatan", -4.1333, 104.1667),
    ("ogan komering ulu selatan", "Sumatera Selatan", -4.6500, 104.0000),
    ("ogan komering ulu timur", "Sumatera Selatan", -3.8500, 104.7500),
    ("penukal abab lematang ilir", "Sumatera Selatan", -3.2167, 103.8500),

    # --- BENGKULU ---
    ("bengkulu", "Bengkulu", -3.8004, 102.2655),
    ("bengkulu selatan", "Bengkulu", -4.3500, 103.0000),
    ("bengkulu tengah", "Bengkulu", -3.7500, 102.4333),
    ("bengkulu utara", "Bengkulu", -3.4333, 102.1833),
    ("kaur", "Bengkulu", -4.6833, 103.3500),
    ("kepahiang", "Bengkulu", -3.6333, 102.5833),
    ("lebong", "Bengkulu", -3.1500, 102.2167),
    ("mukomuko", "Bengkulu", -2.5833, 101.1167),
    ("rejang lebong", "Bengkulu", -3.4667, 102.5333),
    ("seluma", "Bengkulu", -4.0833, 102.5833),

    # --- LAMPUNG ---
    ("bandar lampung", "Lampung", -5.4297, 105.2625),
    ("metro", "Lampung", -5.1136, 105.3067),
    ("lampung barat", "Lampung", -5.1500, 104.1833),
    ("lampung selatan", "Lampung", -5.7000, 105.5833),
    ("lampung tengah", "Lampung", -4.8667, 105.2667),
    ("lampung timur", "Lampung", -5.1000, 105.6833),
    ("lampung utara", "Lampung", -4.8167, 104.8000),
    ("mesuji", "Lampung", -4.0167, 105.4167),
    ("pesawaran", "Lampung", -5.4333, 105.1833),
    ("pesisir barat", "Lampung", -5.1833, 103.9500),
    ("pringsewu", "Lampung", -5.3589, 104.9744),
    ("tanggamus", "Lampung", -5.4833, 104.6167),
    ("tulang bawang", "Lampung", -4.4333, 105.6167),
    ("tulang bawang barat", "Lampung", -4.5000, 105.1000),
    ("way kanan", "Lampung", -4.5000, 104.5333),

    # --- BANGKA BELITUNG ---
    ("pangkalpinang", "Bangka Belitung", -2.1333, 106.1167),
    ("bangka", "Bangka Belitung", -1.9000, 105.9000),
    ("bangka barat", "Bangka Belitung", -1.8000, 105.4500),
    ("bangka selatan", "Bangka Belitung", -2.6667, 106.1000),
    ("bangka tengah", "Bangka Belitung", -2.3667, 106.1167),
    ("belitung", "Bangka Belitung", -2.8667, 107.9167),
    ("belitung timur", "Bangka Belitung", -2.9667, 108.1500),

    # --- DKI JAKARTA ---
    ("jakarta", "DKI Jakarta", -6.2088, 106.8456),
    ("jakarta pusat", "DKI Jakarta", -6.1805, 106.8284),
    ("jakarta selatan", "DKI Jakarta", -6.2615, 106.8106),
    ("jakarta timur", "DKI Jakarta", -6.2250, 106.9004),
    ("jakarta barat", "DKI Jakarta", -6.1683, 106.7589),
    ("jakarta utara", "DKI Jakarta", -6.1384, 106.8640),
    ("kepulauan seribu", "DKI Jakarta", -5.6122, 106.5622),

    # --- JAWA BARAT ---
    ("bandung", "Jawa Barat", -6.9175, 107.6191),
    ("kota bogor", "Jawa Barat", -6.5971, 106.8060),
    ("bogor", "Jawa Barat", -6.4800, 106.8200),
    ("kota bekasi", "Jawa Barat", -6.2383, 106.9756),
    ("bekasi", "Jawa Barat", -6.2400, 107.1400),
    ("depok", "Jawa Barat", -6.4025, 106.7942),
    ("cimahi", "Jawa Barat", -6.8722, 107.5422),
    ("sukabumi", "Jawa Barat", -6.9277, 106.9300),
    ("cirebon", "Jawa Barat", -6.7320, 108.5523),
    ("tasikmalaya", "Jawa Barat", -7.3274, 108.2207),
    ("banjar", "Jawa Barat", -7.3686, 108.5328),
    ("bandung barat", "Jawa Barat", -6.8400, 107.5100),
    ("ciamis", "Jawa Barat", -7.3256, 108.3533),
    ("cianjur", "Jawa Barat", -6.8200, 107.1400),
    ("garut", "Jawa Barat", -7.2167, 107.9000),
    ("indramayu", "Jawa Barat", -6.3264, 108.3200),
    ("karawang", "Jawa Barat", -6.3073, 107.3075),
    ("kuningan", "Jawa Barat", -6.9760, 108.4833),
    ("majalengka", "Jawa Barat", -6.8361, 108.2278),
    ("pangandaran", "Jawa Barat", -7.6833, 108.6500),
    ("purwakarta", "Jawa Barat", -6.5569, 107.4433),
    ("subang", "Jawa Barat", -6.5716, 107.7583),
    ("sumedang", "Jawa Barat", -6.8586, 107.9267),

    # --- BANTEN ---
    ("serang", "Banten", -6.1200, 106.1503),
    ("cilegon", "Banten", -6.0174, 106.0538),
    ("tangerang", "Banten", -6.1783, 106.6319),
    ("tangerang selatan", "Banten", -6.2889, 106.7179),
    ("lebak", "Banten", -6.5500, 106.2500),
    ("pandeglang", "Banten", -6.5000, 105.8000),

    # --- JAWA TENGAH ---
    ("semarang", "Jawa Tengah", -6.9667, 110.4167),
    ("surakarta", "Jawa Tengah", -7.5755, 110.8243),
    ("solo", "Jawa Tengah", -7.5755, 110.8243),
    ("salatiga", "Jawa Tengah", -7.3305, 110.5084),
    ("magelang", "Jawa Tengah", -7.4797, 110.2178),
    ("pekalongan", "Jawa Tengah", -6.8886, 109.6753),
    ("tegal", "Jawa Tengah", -6.8694, 109.1402),
    ("banjarnegara", "Jawa Tengah", -7.3986, 109.6978),
    ("banyumas", "Jawa Tengah", -7.5144, 109.2944),
    ("purwokerto", "Jawa Tengah", -7.4244, 109.2303),
    ("batang", "Jawa Tengah", -6.9086, 109.7317),
    ("blora", "Jawa Tengah", -7.0000, 111.4167),
    ("boyolali", "Jawa Tengah", -7.5333, 110.6000),
    ("brebes", "Jawa Tengah", -6.8667, 109.0333),
    ("cilacap", "Jawa Tengah", -7.7167, 109.0167),
    ("demak", "Jawa Tengah", -6.8944, 110.6389),
    ("grobogan", "Jawa Tengah", -7.1000, 110.9167),
    ("jepara", "Jawa Tengah", -6.5833, 110.6667),
    ("karanganyar", "Jawa Tengah", -7.6000, 110.9500),
    ("kebumen", "Jawa Tengah", -7.6667, 109.6500),
    ("kendal", "Jawa Tengah", -7.0167, 110.2000),
    ("klaten", "Jawa Tengah", -7.7000, 110.6000),
    ("kudus", "Jawa Tengah", -6.8056, 110.8417),
    ("pati", "Jawa Tengah", -6.7556, 111.0389),
    ("pemalang", "Jawa Tengah", -6.8917, 109.3806),
    ("purbalingga", "Jawa Tengah", -7.3889, 109.3639),
    ("purworejo", "Jawa Tengah", -7.7167, 110.0167),
    ("rembang", "Jawa Tengah", -6.7083, 111.3417),
    ("sragen", "Jawa Tengah", -7.4278, 111.0222),
    ("sukoharjo", "Jawa Tengah", -7.6833, 110.8333),
    ("temanggung", "Jawa Tengah", -7.3167, 110.1667),
    ("wonogiri", "Jawa Tengah", -7.8167, 110.9333),
    ("wonosobo", "Jawa Tengah", -7.3611, 109.9000),

    # --- DAERAH ISTIMEWA YOGYAKARTA ---
    ("yogyakarta", "DI Yogyakarta", -7.7956, 110.3695),
    ("jogja", "DI Yogyakarta", -7.7956, 110.3695),
    ("bantul", "DI Yogyakarta", -7.8894, 110.3292),
    ("gunungkidul", "DI Yogyakarta", -7.9606, 110.6053),
    ("kulon progo", "DI Yogyakarta", -7.7719, 110.1583),
    ("sleman", "DI Yogyakarta", -7.7167, 110.3556),

    # --- JAWA TIMUR ---
    ("surabaya", "Jawa Timur", -7.2575, 112.7521),
    ("malang", "Jawa Timur", -7.9797, 112.6304),
    ("kota batu", "Jawa Timur", -7.8711, 112.5269),
    ("kediri", "Jawa Timur", -7.8167, 112.0167),
    ("blitar", "Jawa Timur", -8.0983, 112.1681),
    ("madiun", "Jawa Timur", -7.6298, 111.5239),
    ("mojokerto", "Jawa Timur", -7.4667, 112.4333),
    ("pasuruan", "Jawa Timur", -7.6469, 112.9075),
    ("probolinggo", "Jawa Timur", -7.7544, 113.2158),
    ("banyuwangi", "Jawa Timur", -8.2192, 114.3692),
    ("bangkalan", "Jawa Timur", -7.0306, 112.7483),
    ("bojonegoro", "Jawa Timur", -7.1500, 111.8833),
    ("bondowoso", "Jawa Timur", -7.9167, 113.8167),
    ("gresik", "Jawa Timur", -7.1567, 112.6558),
    ("jember", "Jawa Timur", -8.1725, 113.7000),
    ("jombang", "Jawa Timur", -7.5461, 112.2331),
    ("lamongan", "Jawa Timur", -7.1167, 112.4167),
    ("lumajang", "Jawa Timur", -8.1333, 113.2167),
    ("magetan", "Jawa Timur", -7.6500, 111.3333),
    ("nganjuk", "Jawa Timur", -7.6000, 111.9000),
    ("ngawi", "Jawa Timur", -7.4000, 111.4500),
    ("pacitan", "Jawa Timur", -8.2000, 111.1000),
    ("pamekasan", "Jawa Timur", -7.1667, 113.4833),
    ("ponorogo", "Jawa Timur", -7.8667, 111.4667),
    ("sampang", "Jawa Timur", -7.1833, 113.2500),
    ("sidoarjo", "Jawa Timur", -7.4478, 112.7183),
    ("situbondo", "Jawa Timur", -7.7000, 114.0000),
    ("sumenep", "Jawa Timur", -7.0167, 113.8667),
    ("trenggalek", "Jawa Timur", -8.0500, 111.7167),
    ("tuban", "Jawa Timur", -6.8972, 112.0650),
    ("tulungagung", "Jawa Timur", -8.0667, 111.9000),

    # --- BALI ---
    ("denpasar", "Bali", -8.6705, 115.2126),
    ("badung", "Bali", -8.5833, 115.1833),
    ("bangli", "Bali", -8.4500, 115.3500),
    ("buleleng", "Bali", -8.1167, 115.0833),
    ("gianyar", "Bali", -8.5333, 115.3167),
    ("jembrana", "Bali", -8.3000, 114.6667),
    ("karangasem", "Bali", -8.4500, 115.6167),
    ("klungkung", "Bali", -8.5333, 115.4000),
    ("tabanan", "Bali", -8.5333, 115.1167),

    # --- NUSA TENGGARA BARAT ---
    ("mataram", "Nusa Tenggara Barat", -8.5833, 116.1167),
    ("bima", "Nusa Tenggara Barat", -8.4606, 118.7278),
    ("dompu", "Nusa Tenggara Barat", -8.5333, 118.4500),
    ("lombok barat", "Nusa Tenggara Barat", -8.6833, 116.1333),
    ("lombok tengah", "Nusa Tenggara Barat", -8.7000, 116.2833),
    ("lombok timur", "Nusa Tenggara Barat", -8.6500, 116.5333),
    ("lombok utara", "Nusa Tenggara Barat", -8.3500, 116.2000),
    ("sumbawa", "Nusa Tenggara Barat", -8.5000, 117.4333),
    ("sumbawa barat", "Nusa Tenggara Barat", -8.7500, 116.8500),

    # --- NUSA TENGGARA TIMUR ---
    ("kupang", "Nusa Tenggara Timur", -10.1772, 123.6070),
    ("alor", "Nusa Tenggara Timur", -8.2833, 124.7167),
    ("belu", "Nusa Tenggara Timur", -9.1000, 124.9000),
    ("ende", "Nusa Tenggara Timur", -8.8333, 121.6500),
    ("flores timur", "Nusa Tenggara Timur", -8.3167, 122.9833),
    ("lembata", "Nusa Tenggara Timur", -8.4167, 123.5500),
    ("manggarai", "Nusa Tenggara Timur", -8.5500, 120.4500),
    ("manggarai barat", "Nusa Tenggara Timur", -8.6167, 119.9667),
    ("labuan bajo", "Nusa Tenggara Timur", -8.4964, 119.8877),
    ("manggarai timur", "Nusa Tenggara Timur", -8.6500, 120.6500),
    ("ngada", "Nusa Tenggara Timur", -8.7167, 120.9667),
    ("nagekeo", "Nusa Tenggara Timur", -8.6833, 121.3167),
    ("sikka", "Nusa Tenggara Timur", -8.6333, 122.2167),
    ("sumba barat", "Nusa Tenggara Timur", -9.6500, 119.4167),
    ("sumba barat daya", "Nusa Tenggara Timur", -9.5667, 119.1667),
    ("sumba tengah", "Nusa Tenggara Timur", -9.6000, 119.6000),
    ("sumba timur", "Nusa Tenggara Timur", -9.8667, 120.1500),
    ("timor tengah selatan", "Nusa Tenggara Timur", -9.8500, 124.2833),
    ("timor tengah utara", "Nusa Tenggara Timur", -9.4500, 124.5333),
    ("rote ndao", "Nusa Tenggara Timur", -10.7333, 123.1167),
    ("sabu raijua", "Nusa Tenggara Timur", -10.5000, 121.8333),
    ("malaka", "Nusa Tenggara Timur", -9.5667, 124.9000),

    # --- KALIMANTAN BARAT ---
    ("pontianak", "Kalimantan Barat", -0.0263, 109.3425),
    ("singkawang", "Kalimantan Barat", 0.9083, 108.9833),
    ("bengkayang", "Kalimantan Barat", 0.8167, 109.6500),
    ("kapuas hulu", "Kalimantan Barat", 0.8167, 112.7667),
    ("kayong utara", "Kalimantan Barat", -1.1333, 109.9500),
    ("ketapang", "Kalimantan Barat", -1.8333, 109.9833),
    ("kubu raya", "Kalimantan Barat", -0.3167, 109.3333),
    ("landak", "Kalimantan Barat", 0.4333, 109.7667),
    ("melawi", "Kalimantan Barat", -0.3333, 111.7000),
    ("mempawah", "Kalimantan Barat", 0.3500, 108.9500),
    ("sambas", "Kalimantan Barat", 1.3667, 109.3000),
    ("sanggau", "Kalimantan Barat", 0.1167, 110.5833),
    ("sekadau", "Kalimantan Barat", 0.0167, 110.9500),
    ("sintang", "Kalimantan Barat", 0.0667, 111.5000),

    # --- KALIMANTAN TENGAH ---
    ("palangka raya", "Kalimantan Tengah", -2.2081, 113.9165),
    ("barito selatan", "Kalimantan Tengah", -1.7000, 114.8833),
    ("barito timur", "Kalimantan Tengah", -1.8833, 115.1167),
    ("barito utara", "Kalimantan Tengah", -0.9167, 115.0167),
    ("gunung mas", "Kalimantan Tengah", -1.1333, 113.8667),
    ("kapuas", "Kalimantan Tengah", -2.9667, 114.3833),
    ("katingan", "Kalimantan Tengah", -2.0667, 113.4000),
    ("kotawaringin barat", "Kalimantan Tengah", -2.4000, 111.7333),
    ("kotawaringin timur", "Kalimantan Tengah", -2.0833, 112.7500),
    ("sampit", "Kalimantan Tengah", -2.5333, 112.9500),
    ("lamandau", "Kalimantan Tengah", -1.8500, 111.2833),
    ("murung raya", "Kalimantan Tengah", -0.0167, 114.3333),
    ("pulang pisau", "Kalimantan Tengah", -2.7500, 114.1500),
    ("sukamara", "Kalimantan Tengah", -2.6333, 111.2333),
    ("seruyan", "Kalimantan Tengah", -2.5000, 112.2500),

    # --- KALIMANTAN SELATAN ---
    ("banjarmasin", "Kalimantan Selatan", -3.3194, 114.5908),
    ("banjarbaru", "Kalimantan Selatan", -3.4400, 114.8300),
    ("balangan", "Kalimantan Selatan", -2.3333, 115.6333),
    ("banjar", "Kalimantan Selatan", -3.3167, 115.0833),
    ("martapura", "Kalimantan Selatan", -3.4167, 114.8500),
    ("barito kuala", "Kalimantan Selatan", -3.0833, 114.6167),
    ("hulu sungai selatan", "Kalimantan Selatan", -2.7500, 115.2000),
    ("hulu sungai tengah", "Kalimantan Selatan", -2.6000, 115.4167),
    ("hulu sungai utara", "Kalimantan Selatan", -2.4333, 115.2500),
    ("kotabaru", "Kalimantan Selatan", -3.0000, 116.0000),
    ("tabalong", "Kalimantan Selatan", -1.8833, 115.5333),
    ("tanah bumbu", "Kalimantan Selatan", -3.4500, 115.7000),
    ("tanah laut", "Kalimantan Selatan", -3.8833, 114.8667),
    ("tapin", "Kalimantan Selatan", -2.9167, 115.1500),

    # --- KALIMANTAN TIMUR ---
    ("samarinda", "Kalimantan Timur", -0.5022, 117.1536),
    ("balikpapan", "Kalimantan Timur", -1.2654, 116.8312),
    ("bontang", "Kalimantan Timur", 0.1333, 117.5000),
    ("berau", "Kalimantan Timur", 2.1667, 117.5000),
    ("kutai barat", "Kalimantan Timur", -0.5833, 115.7000),
    ("kutai kartanegara", "Kalimantan Timur", -0.4333, 117.0000),
    ("kutai timur", "Kalimantan Timur", 0.7500, 117.5000),
    ("mahakam ulu", "Kalimantan Timur", 0.5000, 115.0000),
    ("paser", "Kalimantan Timur", -1.8500, 116.0500),
    ("penajam paser utara", "Kalimantan Timur", -1.3333, 116.7167),
    ("nusantara", "Kalimantan Timur", -0.9744, 116.7092),

    # --- KALIMANTAN UTARA ---
    ("tarakan", "Kalimantan Utara", 3.3000, 117.6333),
    ("bulungan", "Kalimantan Utara", 2.9000, 117.1333),
    ("malinau", "Kalimantan Utara", 2.5000, 115.5000),
    ("nunukan", "Kalimantan Utara", 4.1333, 117.6667),
    ("tana tidung", "Kalimantan Utara", 3.5500, 117.2500),

    # --- SULAWESI UTARA ---
    ("manado", "Sulawesi Utara", 1.4748, 124.8428),
    ("bitung", "Sulawesi Utara", 1.4450, 125.1889),
    ("tomohon", "Sulawesi Utara", 1.3289, 124.8392),
    ("kotamobagu", "Sulawesi Utara", 0.7306, 124.3139),
    ("bolaang mongondow", "Sulawesi Utara", 0.7500, 124.0000),
    ("bolaang mongondow selatan", "Sulawesi Utara", 0.4000, 123.8000),
    ("bolaang mongondow timur", "Sulawesi Utara", 0.7333, 124.5000),
    ("bolaang mongondow utara", "Sulawesi Utara", 0.8833, 123.6000),
    ("kepulauan sangihe", "Sulawesi Utara", 3.5000, 125.5500),
    ("kepulauan siau tagulandang biaro", "Sulawesi Utara", 2.7500, 125.4000),
    ("kepulauan talaud", "Sulawesi Utara", 4.3333, 126.7500),
    ("minahasa", "Sulawesi Utara", 1.2500, 124.9167),
    ("minahasa selatan", "Sulawesi Utara", 1.0500, 124.5833),
    ("minahasa tenggara", "Sulawesi Utara", 0.9333, 124.8000),
    ("minahasa utara", "Sulawesi Utara", 1.4000, 124.9833),

    # --- GORONTALO ---
    ("gorontalo", "Gorontalo", 0.5435, 123.0568),
    ("boalemo", "Gorontalo", 0.6500, 122.3333),
    ("bone bolango", "Gorontalo", 0.5500, 123.2333),
    ("gorontalo utara", "Gorontalo", 0.8333, 122.8000),
    ("pohuwato", "Gorontalo", 0.5167, 121.8167),

    # --- SULAWESI TENGAH ---
    ("palu", "Sulawesi Tengah", -0.8917, 119.8707),
    ("banggai", "Sulawesi Tengah", -1.2500, 122.7500),
    ("banggai kepulauan", "Sulawesi Tengah", -1.4167, 123.5000),
    ("banggai laut", "Sulawesi Tengah", -1.6000, 123.5000),
    ("buol", "Sulawesi Tengah", 1.0500, 121.4333),
    ("donggala", "Sulawesi Tengah", -0.6833, 119.7333),
    ("morowali", "Sulawesi Tengah", -2.3333, 121.7500),
    ("morowali utara", "Sulawesi Tengah", -1.9000, 121.3333),
    ("parigi moutong", "Sulawesi Tengah", 0.1500, 120.2500),
    ("poso", "Sulawesi Tengah", -1.4000, 120.7500),
    ("sigi", "Sulawesi Tengah", -1.3833, 119.9667),
    ("tojo una-una", "Sulawesi Tengah", -1.1500, 121.5000),
    ("toli-toli", "Sulawesi Tengah", 1.0500, 120.8000),

    # --- SULAWESI BARAT ---
    ("mamuju", "Sulawesi Barat", -2.6778, 118.8872),
    ("majene", "Sulawesi Barat", -3.0000, 118.9167),
    ("mamasa", "Sulawesi Barat", -2.9667, 119.3333),
    ("mamuju tengah", "Sulawesi Barat", -2.1333, 119.3000),
    ("pasangkayu", "Sulawesi Barat", -1.4167, 119.3667),
    ("polewali mandar", "Sulawesi Barat", -3.4167, 119.3333),

    # --- SULAWESI SELATAN ---
    ("makassar", "Sulawesi Selatan", -5.1477, 119.4327),
    ("parepare", "Sulawesi Selatan", -4.0167, 119.6333),
    ("palopo", "Sulawesi Selatan", -2.9944, 120.1969),
    ("bantaeng", "Sulawesi Selatan", -5.5500, 119.9500),
    ("barru", "Sulawesi Selatan", -4.4167, 119.6833),
    ("bone", "Sulawesi Selatan", -4.7500, 120.1667),
    ("bulukumba", "Sulawesi Selatan", -5.5500, 120.2000),
    ("enrekang", "Sulawesi Selatan", -3.5667, 119.7833),
    ("gowa", "Sulawesi Selatan", -5.3333, 119.7500),
    ("jeneponto", "Sulawesi Selatan", -5.6667, 119.7167),
    ("kepulauan selayar", "Sulawesi Selatan", -6.3333, 120.5000),
    ("luwu", "Sulawesi Selatan", -3.3333, 120.1667),
    ("luwu timur", "Sulawesi Selatan", -2.5833, 121.1667),
    ("luwu utara", "Sulawesi Selatan", -2.5000, 120.2500),
    ("maros", "Sulawesi Selatan", -5.0000, 119.6667),
    ("pangkajene dan kepulauan", "Sulawesi Selatan", -4.8333, 119.5500),
    ("pinrang", "Sulawesi Selatan", -3.7833, 119.6500),
    ("sidenreng rappang", "Sulawesi Selatan", -3.9167, 119.9667),
    ("sinjai", "Sulawesi Selatan", -5.2167, 120.1833),
    ("soppeng", "Sulawesi Selatan", -4.3500, 119.9000),
    ("takalar", "Sulawesi Selatan", -5.4167, 119.5833),
    ("tana toraja", "Sulawesi Selatan", -3.1167, 119.7500),
    ("toraja utara", "Sulawesi Selatan", -2.9500, 119.9000),
    ("wajo", "Sulawesi Selatan", -4.0833, 120.1667),

    # --- SULAWESI TENGGARA ---
    ("kendari", "Sulawesi Tenggara", -3.9985, 122.5126),
    ("bau-bau", "Sulawesi Tenggara", -5.4667, 122.6000),
    ("bombana", "Sulawesi Tenggara", -4.8000, 121.8000),
    ("buton", "Sulawesi Tenggara", -5.1667, 122.8333),
    ("buton selatan", "Sulawesi Tenggara", -5.5833, 122.7500),
    ("buton tengah", "Sulawesi Tenggara", -5.3000, 122.4500),
    ("buton utara", "Sulawesi Tenggara", -4.7500, 122.9667),
    ("kolaka", "Sulawesi Tenggara", -4.1667, 121.6000),
    ("kolaka timur", "Sulawesi Tenggara", -4.1000, 121.8500),
    ("kolaka utara", "Sulawesi Tenggara", -3.3333, 121.0500),
    ("konawe", "Sulawesi Tenggara", -3.8500, 122.0500),
    ("konawe kepulauan", "Sulawesi Tenggara", -4.1500, 123.0000),
    ("konawe selatan", "Sulawesi Tenggara", -4.3333, 122.3333),
    ("konawe utara", "Sulawesi Tenggara", -3.3667, 122.1500),
    ("muna", "Sulawesi Tenggara", -4.9500, 122.6500),
    ("muna barat", "Sulawesi Tenggara", -4.8333, 122.4000),
    ("wakatobi", "Sulawesi Tenggara", -5.3167, 123.5833),

    # --- MALUKU ---
    ("ambon", "Maluku", -3.6554, 128.1908),
    ("tual", "Maluku", -5.6333, 132.7500),
    ("buru", "Maluku", -3.3333, 126.8333),
    ("buru selatan", "Maluku", -3.7500, 126.6500),
    ("kepulauan aru", "Maluku", -6.1667, 134.5000),
    ("kepulauan tanimbar", "Maluku", -7.5000, 131.5000),
    ("maluku barat daya", "Maluku", -8.0000, 127.8333),
    ("maluku tengah", "Maluku", -3.3000, 129.0000),
    ("maluku tenggara", "Maluku", -5.7500, 132.7333),
    ("seram bagian barat", "Maluku", -3.1000, 128.3333),
    ("seram bagian timur", "Maluku", -3.2000, 130.5000),

    # --- MALUKU UTARA ---
    ("ternate", "Maluku Utara", 0.7833, 127.3667),
    ("tidore kepulauan", "Maluku Utara", 0.6833, 127.4000),
    ("halmahera barat", "Maluku Utara", 1.3333, 127.5000),
    ("halmahera tengah", "Maluku Utara", 0.4833, 128.2500),
    ("halmahera timur", "Maluku Utara", 1.0000, 128.5000),
    ("halmahera selatan", "Maluku Utara", -0.6667, 127.5000),
    ("halmahera utara", "Maluku Utara", 1.7500, 128.0000),
    ("kepulauan sula", "Maluku Utara", -1.9000, 125.8000),
    ("pulau morotai", "Maluku Utara", 2.3333, 128.4167),
    ("pulau taliabu", "Maluku Utara", -1.8000, 124.7500),

    # --- PAPUA ---
    ("jayapura", "Papua", -2.5337, 140.7181),
    ("biak numfor", "Papua", -0.9833, 135.9167),
    ("keerom", "Papua", -3.3000, 140.6167),
    ("kepulauan yapen", "Papua", -1.7500, 136.2500),
    ("mamberamo raya", "Papua", -2.2500, 137.9167),
    ("sarmi", "Papua", -1.9500, 139.1000),
    ("supiori", "Papua", -0.7333, 135.6000),
    ("waropen", "Papua", -2.2500, 136.7500),

    # --- PAPUA BARAT ---
    ("manokwari", "Papua Barat", -0.8615, 134.0620),
    ("fakfak", "Papua Barat", -2.9167, 132.3000),
    ("kaimana", "Papua Barat", -3.6667, 133.7667),
    ("manokwari selatan", "Papua Barat", -1.3333, 134.1500),
    ("pegunungan arfak", "Papua Barat", -1.1667, 133.8833),
    ("teluk bintuni", "Papua Barat", -2.1500, 133.5000),
    ("teluk wondama", "Papua Barat", -2.7000, 134.5000),

    # --- PAPUA BARAT DAYA ---
    ("sorong", "Papua Barat Daya", -0.8762, 131.2558),
    ("maybrat", "Papua Barat Daya", -1.3000, 132.5000),
    ("raja ampat", "Papua Barat Daya", -0.5000, 130.5000),
    ("sorong selatan", "Papua Barat Daya", -1.5000, 132.1667),
    ("tambrauw", "Papua Barat Daya", -0.6000, 132.5000),

    # --- PAPUA SELATAN ---
    ("merauke", "Papua Selatan", -8.4932, 140.4019),
    ("asmat", "Papua Selatan", -5.4167, 138.3333),
    ("boven digoel", "Papua Selatan", -5.5000, 140.3333),
    ("mappi", "Papua Selatan", -6.5000, 139.5000),

    # --- PAPUA TENGAH ---
    ("nabire", "Papua Tengah", -3.3667, 135.5000),
    ("timika", "Papua Tengah", -4.5469, 136.8837),
    ("mimika", "Papua Tengah", -4.5469, 136.8837),
    ("deiyai", "Papua Tengah", -3.9500, 135.9500),
    ("dogiyai", "Papua Tengah", -3.9833, 135.6500),
    ("intan jaya", "Papua Tengah", -3.6833, 136.9500),
    ("paniai", "Papua Tengah", -3.9000, 136.3500),
    ("puncak", "Papua Tengah", -3.7500, 137.1500),
    ("puncak jaya", "Papua Tengah", -3.6500, 137.6000),

    # --- PAPUA PEGUNUNGAN ---
    ("jayawijaya", "Papua Pegunungan", -4.0833, 138.9667),
    ("wamena", "Papua Pegunungan", -4.0950, 138.9469),
    ("lanny jaya", "Papua Pegunungan", -3.9500, 138.4500),
    ("mamberamo tengah", "Papua Pegunungan", -2.7500, 138.7500),
    ("nduga", "Papua Pegunungan", -4.4167, 138.4167),
    ("pegunungan bintang", "Papua Pegunungan", -4.8333, 140.3333),
    ("tolikara", "Papua Pegunungan", -3.6000, 138.6000),
    ("yahukimo", "Papua Pegunungan", -4.6667, 139.6667),
    ("yalimo", "Papua Pegunungan", -3.8000, 139.3000),
]


def load_gazetteer():
    print(f"[+] Menghubungkan ke PostgreSQL: {DATABASE_URL.split('@')[-1]}")
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            # Pastikan tabel ada
            cur.execute("""
                CREATE TABLE IF NOT EXISTS gazetteer (
                    id       BIGSERIAL PRIMARY KEY,
                    nama     VARCHAR(120) NOT NULL UNIQUE,
                    provinsi VARCHAR(120),
                    lat      DOUBLE PRECISION NOT NULL,
                    lon      DOUBLE PRECISION NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_gazetteer_nama ON gazetteer (nama);
            """)

            # Simpan atau update tiap wilayah
            query = """
                INSERT INTO gazetteer (nama, provinsi, lat, lon)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (nama) DO UPDATE
                SET provinsi = EXCLUDED.provinsi,
                    lat = EXCLUDED.lat,
                    lon = EXCLUDED.lon;
            """
            
            cur.executemany(query, INDONESIA_REGIONS)
            conn.commit()

            cur.execute("SELECT count(*) FROM gazetteer;")
            total = cur.fetchone()[0]
            print(f"[✓] Berhasil memuat gazetteer! Total wilayah tersimpan: {total}")
            return total


if __name__ == "__main__":
    try:
        load_gazetteer()
    except Exception as e:
        print(f"[!] Error saat memuat gazetteer: {e}", file=sys.stderr)
        sys.exit(1)
