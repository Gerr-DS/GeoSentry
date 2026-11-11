# Riset: Platform Pendeteksi Hoaks/Disinformasi yang Mengambil Data dari TikTok, X, Instagram, dan Media Sosial Lainnya

- Tanggal: 2026-11-11
- Sumber pencarian: Agent Reach (channel web — Exa semantic search + Jina page read), GitHub page reads
- Fokus: platform GRATIS, termasuk open source / scraping tanpa API resmi (kategori berisiko ikut disertakan)
- Konteks: referensi untuk GeoSentry (zero-token policy, incremental ML, WebGIS)

---

## A. Web Tool Gratis — Paste Link, Tanpa Install

| # | Nama | URL | Sumber data | Keterangan |
|---|------|-----|-------------|------------|
| 1 | Truvizy | https://truvizy.app | TikTok, YouTube, Instagram, X + sembarang URL | Scam/deepfake/trust score. Free, tanpa signup. |
| 2 | VidVerdict | https://www.vidverdict.com | TikTok, YouTube, Instagram, X, Facebook | Transkrip + cek klaim. Gratis untuk cek pertama, tanpa akun. |
| 3 | TruthLens | https://www.truthlens.online | YouTube, TikTok, Instagram, Facebook, X | 8 tools gratis (video legitimacy, AI image, SIFT, source credibility, dst). Tanpa signup. |
| 4 | Crackly | https://crackly-ai.com/id | Instagram, YouTube, X, TikTok, Facebook + video WhatsApp | Deteksi video AI, gratis. |
| 5 | VerifyReels | https://verifyreels.com | Instagram, YouTube, Facebook, X, TikTok | 10 kredit/hari gratis; ada bot WhatsApp. |
| 6 | VerifAI | https://verifai-it.com | Instagram Reels, TikTok | Early access, gratis. |
| 7 | FAXTR | https://faxtr.com | TikTok, Instagram, YouTube | Baca pidato + teks on-screen + frame; cross-check AFP/Reuters/Snopes/PolitiFact. 1 scan gratis/hari. |
| 8 | Facticity.AI | https://www.facticity.ai | Instagram, TikTok, YouTube | Multibahasa, cek teks/audio/video real-time. |
| 9 | Detectly | https://getaidetect.com/id | TikTok, Instagram, Facebook | Bahasa Indonesia, gratis. |
| 10 | DeepGuard AI | https://deepguardai.com/deepfake-checker | Upload video | Deepfake checker, gratis, tanpa signup. |
| 11 | CheckReal.ai | https://checkreal.ai/video-detector | Upload video / URL | Deteksi deepfake/edit/context-risk. |
| 12 | HumanMeter | https://humanmeter.app | Link video | Cek video AI, gratis. |
| 13 | Bitdefender RealCheck | https://www.bitdefender.com/en-us/consumer/realcheck | TikTok, YouTube, Instagram, Facebook, X | Deepfake check dari link. |
| 14 | Justifact | https://justifact.eu | TikTok, Reels, Shorts, Facebook | Verdict per klaim, gratis. |
| 15 | SUS IT | https://susit.ai/link | TikTok, Instagram | Cek AI-generated video. |

## B. Open Source / Self-Host (Gratis, Berisiko: Scraping Tanpa API Resmi)

| # | Nama | URL | Lisensi | Sumber data | Keterangan |
|---|------|-----|---------|-------------|------------|
| 1 | Eden | https://github.com/imMohammedSahil/Eden | MIT | Instagram Reels & video | Pipeline: yt-dlp + instaloader + Playwright → OpenCV → Whisper → EasyOCR → Gemini. Paling lengkap untuk IG. |
| 2 | Avocado | https://github.com/lukajvnic/Avocado | — | TikTok | Chrome extension + FastAPI; Supadata API + Gemini/OpenRouter. Perlu API key (ada free tier). |
| 3 | CrediClip | https://github.com/aliraza2025/CrediClip | — | TikTok, Instagram, YouTube Shorts | FastAPI, OCR, ASR, credibility score 0-100, risk flags. |
| 4 | FactFlux | https://github.com/MeirKaD/FactFlux | MIT | TikTok, Instagram, X, Facebook, YouTube, LinkedIn | Multi-agent fact-checking via Bright Data + Agno framework. |
| 5 | checkmate | https://github.com/MohtashamMurshid/checkmate | — | TikTok, Twitter/X, web | NLP + fact-checking + credibility scoring (Malaysia). |
| 6 | VeriFact | https://github.com/AravindB98/verifact | Apache-2.0 | Link / X post / WhatsApp forward | Live demo gratis tanpa install; provenance, claim verification, source reputation. |
| 7 | PRISM | https://github.com/JerichoDelosReyes/PRISM | — | Media sosial Filipina | Deteksi teks AI, gambar palsu, deepfake video (multimodal deep learning). |
| 8 | Tracebit | https://github.com/Simon39soft/Tracebit | MIT | Webpage, TikTok, Instagram, Facebook | JS framework flag konten AI real-time. Perlu Anthropic API key (free tier). |
| 9 | AI_Slop_Detector | https://github.com/JR000270/AI_Slop_Detector | — | Gambar, video, artikel | Browser extension + local backend, deteksi AI + fact-check. |
| 10 | aidetector | https://github.com/adolfousier/aidetector | — | X/Twitter, Instagram, LinkedIn | Chrome/Firefox extension, OpenRouter LLM. |
| 11 | bullshit-detector | https://github.com/SerhiiKorniienko/bullshit-detector | — | YouTube | Claim-by-claim verification, skor 0-10. |
| 12 | scamira | https://github.com/guernika54/scamira | — | Sosial media Jepang | Deteksi pola penipuan/info-marketing. |

## C. Scraper Gratis (Ambil Data Mentah TikTok/IG/X, Olah Sendiri)

| # | Nama | URL | Sumber data | Keterangan |
|---|------|-----|-------------|------------|
| 1 | yt-dlp | https://github.com/yt-dlp/yt-dlp | TikTok, Instagram, X, YouTube, Facebook | Standar, gratis, tanpa API key. |
| 2 | instaloader | https://github.com/instaloader/instaloader | Instagram | Profil, post, reel, story. |
| 3 | instagram-tiktok-scraper | https://github.com/AlbertoQuian/instagram-tiktok-scraper | Instagram, TikTok | GPL v3; Playwright + yt-dlp, untuk riset akademik. |
| 4 | TikTok-Content-Scraper | https://github.com/Q-Bukold/TikTok-Content-Scraper | TikTok | No API key; unduh video MP4 + slide + metadata. |
| 5 | skraper | https://github.com/sokomishalov/skraper | Facebook, Instagram, Twitter, dll | Kotlin/Java, tanpa auth. |
| 6 | the-tet | https://github.com/alexandercroft/the-tet | YouTube, TikTok, Instagram, Threads, Telegram | yt-dlp + gallery-dl + Playwright, downloader universal. |
| 7 | SCrawler | https://github.com/AAndyProgram/SCrawler | Multi-platform | 2.245 stars, downloader. |
| 8 | social-media-downloader | https://github.com/Vette1123/social-media-downloader | TikTok, X, IG, FB, Pinterest, Reddit, Threads, dll | Next.js/React, no ads, no tracking. |

## D. Rekomendasi untuk GeoSentry

Kombinasi paling siap untukGeoSentry (zero-token, risiko ditanggung):

1. Pipeline video: Eden (MIT) sebagai referensi arsitektur IG Reels, atau Avocado untuk TikTok (lebih simpel).
2. Scraper: yt-dlp + instaloader + Playwright — bisa ditambahkan ke `scripts/scraper/` sebagai sumber TikTok/IG, sejalan dengan `free_social_scraper.py` yang sudah ada.
3. Multi-platform: FactFlux (MIT) sebagai referensi arsitektur multi-agent.

Catatan risiko: scraping TikTok/IG melanggar ToS dan berisiko IP block / captcha. Jika butuh stabil, TikTok Research API dan Instagram Graph API punya opsi gratis untuk riset, tapi proses persetujuannya lebih lama.

---

## Lampiran: Catatan Sumber

- Pencarian dilakukan via Agent Reach (Exa search + Jina read) pada 2026-11-11.
- GitHub search via Agent Reach sempat timeout; data repo diambil dari halaman GitHub via Jina read.
- Semua URL di atas bersifat publik dan gratis pada saat riset dilakukan; status free tier bisa berubah sewaktu-waktu.
