"""Search relevant Indonesian news from publisher RSS results and inspect article bodies."""

import json
from functools import lru_cache
import os
import re
import socket
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

import feedparser
import requests
from bs4 import BeautifulSoup

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from scripts.nlp.predict import is_unsourced_criticism, predict_threat
try:
    from Sastrawi.Stemmer.StemmerFactory import StemmerFactory
    STEMMER = StemmerFactory().create_stemmer()
except Exception:  # Keep search usable if the optional stemmer is unavailable.
    STEMMER = None


socket.setdefaulttimeout(8)

TRUSTED_DOMAINS = (
    "antaranews.com", "kompas.com", "tempo.co", "detik.com", "cnnindonesia.com",
    "bbc.com", "bbcindonesia.com", "cnbcindonesia.com", "liputan6.com", "tirto.id",
    "republika.co.id", "kontan.co.id", "bisnis.com", "kompas.tv", "tvonenews.com",
    "tribunnews.com", "suara.com", "idntimes.com", "sindonews.com", "okezone.com",
    "kumparan.com", "jawapos.com", "jpnn.com", "viva.co.id", "mediaindonesia.com",
    "katadata.co.id", "metrotvnews.com", "rri.co.id", "thejakartapost.com",
    "jakartaglobe.id", "voaindonesia.com", "dw.com",
    "kompas.id", "infopublik.id", "mui.or.id", "pks.id", "jambiindependent.disway.id",
    "turnbackhoax.id", "cekfakta.com", "cekfakta.tempo.co", "komdigi.go.id",
    "mafindo.or.id", "kominfo.go.id", "bmkg.go.id", "bps.go.id", "bi.go.id", "kemkes.go.id",
    "kemendagri.go.id", "setneg.go.id", "polri.go.id", "go.id", "ugm.ac.id"
)
FACT_CHECK_DOMAINS = ("turnbackhoax.id", "cekfakta.com", "cekfakta.tempo.co", "mafindo.or.id")
NEWS_DOMAINS = tuple(d for d in TRUSTED_DOMAINS if d not in FACT_CHECK_DOMAINS)

STOP_WORDS = set("""dan yang di ke dari untuk pada adalah itu ini dengan tentang atau serta sebagai
akan sudah telah ada tidak bukan kepada dalam oleh karena agar bisa dapat jadi saat apa siapa
bagaimana berita kabar informasi klaim viral hari ini indonesia""".split())
STOP_WORDS |= {STEMMER.stem(word) for word in tuple(STOP_WORDS)} if STEMMER else set()

# Reusable Indonesian paraphrase map for news queries. Keep this independent of
# any person, event, or example query so the same matching works across topics.
SYNONYM_GROUPS = (
    ("berhenti", "mundur", "mengundurkan diri", "resign", "lengser"),
    ("wapres", "wakil presiden"),
    ("presiden", "kepala negara"),
    ("meninggal", "wafat", "tewas"),
    ("naik", "meningkat", "bertambah", "menanjak", "kenaikan", "peningkatan"),
    ("turun", "menurun", "berkurang"),
    ("melarang", "larangan", "dilarang", "tidak mengizinkan"),
    ("membantah", "menepis", "menyangkal"),
    ("mengatakan", "menyatakan", "menuturkan", "berujar"),
    ("mengumumkan", "memberitahukan", "merilis"),
    ("mencabut", "menarik kembali", "membatalkan"),
    ("menetapkan", "memutuskan", "mengesahkan", "memberlakukan"),
    ("ditangkap", "diamankan", "diciduk", "ditahan"),
    ("kecelakaan", "tabrakan", "lakalantas"),
    ("kebakaran", "terbakar"),
    ("gempa", "gempa bumi"),
    ("bantuan", "bansos", "bantuan sosial"),
    ("pemilu", "pemilihan umum"),
    ("mengizinkan", "membolehkan", "memperbolehkan"),
    ("menolak", "tidak menerima", "menentang"),
    ("demo", "demonstrasi", "unjuk rasa"),
    ("benar", "betul", "sesuai fakta"),
    ("hoaks", "hoax", "kabar bohong", "berita palsu"),
    ("ditunda", "diundur", "tertunda"),
    ("mencalonkan diri", "maju sebagai calon"),
    ("membunuh", "menewaskan", "merenggut nyawa"),
    ("menang", "unggul", "meraih kemenangan"),
    ("kalah", "tumbang", "menelan kekalahan"),
)
CANONICAL = {term: group[0] for group in SYNONYM_GROUPS for term in group}
SYNONYM_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(term) for term in sorted(CANONICAL, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)


def expand_query(query):
    variants = [query.strip()]
    lowered = query.casefold().strip()
    if lowered != variants[0]:
        variants.append(lowered)

    # Replace each recognized concept phrase in any query, while preserving the
    # rest of the user's words and named entities. Searching variants separately
    # avoids brittle, event-specific query templates.
    for group in SYNONYM_GROUPS:
        found = [term for term in sorted(group, key=len, reverse=True)
                 if re.search(r"\b" + re.escape(term) + r"\b", lowered)]
        if not found:
            continue
        for source_term in found:
            for replacement in group:
                if replacement == source_term:
                    continue
                candidate = re.sub(r"\b" + re.escape(source_term) + r"\b", replacement, lowered)
                if candidate not in variants:
                    variants.append(candidate)
                if len(variants) >= 10:
                    return variants
    return variants


@lru_cache(maxsize=4096)
def _canonical_tokens_cached(text, stem):
    value = SYNONYM_PATTERN.sub(lambda match: CANONICAL[match.group(0).casefold()], text.casefold())
    value = re.sub(r"[^\w\s]", " ", value, flags=re.UNICODE)
    tokens = []
    for token in value.split():
        if token in STOP_WORDS:
            continue
        if stem and STEMMER:
            stemmed = STEMMER.stem(token)
            # Sastrawi occasionally over-stems distinct Indonesian words.
            if token not in {"pemerintah", "pemerintahan", "kegiatan"}:
                token = stemmed
        if token in STOP_WORDS:
            continue
        tokens.append(CANONICAL.get(token, token))
    return set(tokens)


def canonical_tokens(text, stem=True):
    return _canonical_tokens_cached(str(text), stem)


def relevance(query, title, body):
    title_score, body_score = relevance_components(query, title, body)
    # Full article text carries most of the claim evidence; keep headline
    # similarity useful without letting paraphrased titles hide matching body.
    return round(min(1.0, title_score * 0.55 + body_score * 0.45), 3)


def relevance_components(query, title, body, stem_body=True):
    q = canonical_tokens(query, stem=True)
    if not q:
        return 0.0, 0.0
    title_tokens = canonical_tokens(title)
    body_tokens = canonical_tokens(body, stem=stem_body)
    title_score = len(q & title_tokens) / len(q)
    body_score = len(q & body_tokens) / len(q)
    return title_score, body_score


def compact_search_query(query):
    """Turn pasted article text into a short claim query for source lookup."""
    if len(canonical_tokens(query)) <= 6:
        return query.strip()
    first_sentence = re.split(r"(?<=[.!?])\s+", query.strip(), maxsplit=1)[0]
    if len(canonical_tokens(first_sentence)) >= 3:
        query_words = []
        for word in re.findall(r"[\w'-]+", first_sentence, flags=re.UNICODE):
            if not canonical_tokens(word):
                continue
            query_words.append(word)
            if len(canonical_tokens(" ".join(query_words))) >= 6:
                break
        return " ".join(query_words)
    words = re.findall(r"[\w'-]+", query, flags=re.UNICODE)
    kept = []
    for word in words:
        if len(canonical_tokens(word)) == 0:
            continue
        kept.append(word)
        if len(canonical_tokens(" ".join(kept))) >= 6:
            break
    return " ".join(kept) or query[:180]


def source_domain(url):
    from urllib.parse import urlparse
    return (urlparse(url).hostname or "").lower().removeprefix("www.")


def publisher_group(domain):
    """Collapse subdomains so one publisher is not counted as several sources."""
    labels = (domain or "").lower().removeprefix("www.").split(".")
    if len(labels) < 2:
        return domain
    if len(labels) >= 3 and labels[-2] in {"co", "com", "gov", "org", "ac", "net", "or"}:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def independent_publisher_groups(articles):
    """Count publisher groups after removing near-identical syndicated copies."""
    ordered = sorted(articles, key=lambda item: float(item.get("relevance") or 0), reverse=True)
    groups = set()
    signatures = []
    for article in ordered:
        domain = article.get("domain") or source_domain(article.get("url", ""))
        group = publisher_group(domain)
        if not group or group in groups:
            continue
        tokens = canonical_tokens(
            f"{article.get('title', '')} {str(article.get('content') or article.get('body') or '')[:1800]}"
        )
        duplicate = False
        if len(tokens) >= 12:
            for previous in signatures:
                union = tokens | previous
                if len(union) >= 12 and len(tokens & previous) / len(union) >= 0.88:
                    duplicate = True
                    break
        if duplicate:
            continue
        groups.add(group)
        if tokens:
            signatures.append(tokens)
    return groups


def is_trusted(domain):
    return any(domain == trusted or domain.endswith("." + trusted) for trusted in TRUSTED_DOMAINS)


def is_fact_check(domain):
    return any(domain == trusted or domain.endswith("." + trusted) for trusted in FACT_CHECK_DOMAINS)


def is_official_primary_domain(domain):
    return domain.endswith((".go.id", ".ac.id", ".gov", ".gov.id"))


def explicit_fact_check_verdict(title, body, domain):
    """Return a verdict only from a clear verdict label or a fact-check conclusion."""
    title = str(title or "")
    body = str(body or "")
    verdict_title = re.sub(r"^\s*(?:cek\s+fakta|periksa\s+fakta|fact\s*check)\s*[:—-]\s*", "", title, flags=re.I)
    false_prefix = re.search(
        r"^\s*(?:\[(?:salah|hoaks|hoax|palsu|tidak benar|keliru|menyesatkan)\]"
        r"|(?:salah|hoaks|hoax|palsu|tidak benar|keliru|menyesatkan)\s*[:,!—-])",
        verdict_title,
        re.I,
    )
    if false_prefix:
        return "hoaks"
    true_prefix = re.search(
        r"^\s*(?:\[(?:benar|fakta|valid)\]|(?:benar|fakta|valid)\s*[:,—-])",
        verdict_title,
        re.I,
    )
    if true_prefix:
        return "fakta"

    # Ordinary reporting often quotes words such as “ijazah palsu” while
    # describing an allegation. Those words alone are not a verdict. Only
    # parse a body verdict from a dedicated fact-check publisher, and only in
    # an explicitly marked conclusion section.
    if not is_fact_check(domain):
        return None

    conclusion = re.search(
        r"\b(?:kesimpulan(?:\s+pemeriksaan)?|hasil\s+(?:pemeriksaan|penelusuran)|putusan\s+cek\s+fakta)\b"
        r"\s*[:—-]?\s*([^\n]{1,500})",
        body,
        re.I,
    )
    if not conclusion:
        return None
    verdict_text = conclusion.group(1)
    false_verdict = re.search(
        r"\b(?:klaim|informasi|narasi|unggahan|konten|berita|kabar)\b[^.!?]{0,180}"
        r"\b(?:ini|itu|tersebut|adalah|merupakan|terbukti|dinyatakan|dipastikan)\b[^.!?]{0,80}"
        r"\b(?:tidak benar|tidak terbukti|tidak didukung bukti|palsu|hoaks|hoax|keliru|salah|menyesatkan)\b",
        verdict_text,
        re.I,
    )
    if false_verdict:
        return "hoaks"
    true_verdict = re.search(
        r"\b(?:klaim|informasi|narasi|unggahan|konten|berita|kabar)\b[^.!?]{0,180}"
        r"\b(?:ini|itu|tersebut|adalah|merupakan|terbukti|dinyatakan|dipastikan)\b[^.!?]{0,80}"
        r"\b(?:benar|sesuai fakta|terbukti benar|valid)\b",
        verdict_text,
        re.I,
    )
    return "fakta" if true_verdict else None


def article_text(url):
    headers = {"User-Agent": "Mozilla/5.0 (compatible; GeoSentry/1.0; +http://localhost:3000)"}
    try:
        response = requests.get(url, headers=headers, timeout=(2, 4), allow_redirects=True)
        response.raise_for_status()
        final_url = response.url
        soup = BeautifulSoup(response.content, "html.parser")
        for node in soup(["script", "style", "nav", "header", "footer", "aside", "form", "noscript"]):
            node.decompose()

        article = soup.find("article")
        root = article or soup
        paragraphs = []
        for p in root.find_all("p"):
            text = re.sub(r"\s+", " ", p.get_text(" ", strip=True))
            if len(text) < 45:
                continue
            linked_chars = sum(len(a.get_text(" ", strip=True)) for a in p.find_all("a"))
            # Publisher pages often append related-story links inside the article
            # element; exclude those so navigation text cannot distort relevance.
            if linked_chars / max(1, len(text)) > 0.40:
                continue
            paragraphs.append(text)
        body = "\n\n".join(dict.fromkeys(paragraphs))[:10000]
        title = ""
        title_node = soup.find("h1") or soup.find("meta", property="og:title")
        if title_node:
            title = title_node.get("content", "") if title_node.name == "meta" else title_node.get_text(" ", strip=True)
        published = ""
        for key in ("article:published_time", "datePublished", "pubdate"):
            node = soup.find("meta", attrs={"property": key}) or soup.find("meta", attrs={"name": key})
            if node and node.get("content"):
                published = node["content"][:40]
                break
        source = ""
        source_node = soup.find("meta", property="og:site_name")
        if source_node:
            source = source_node.get("content", "")
        if len(body) < 280:
            return None
        return {"url": final_url, "title": title, "body": body, "published": published, "source": source}
    except Exception:
        return None


def classify_article(article, domain):
    full_text = f"{article['title']}. {article['body']}"
    prediction = predict_threat(full_text)
    verdict = explicit_fact_check_verdict(article["title"], article["body"], domain)

    if verdict:
        label = verdict
        confidence = max(0.90 if verdict == "hoaks" else 0.88, prediction["probabilitas"])
        basis = "putusan klaim yang dinyatakan eksplisit oleh sumber tepercaya"
    elif is_fact_check(domain) or re.search(r"\b(?:cek\s+fakta|periksa\s+fakta|tim\s+pemeriksa\s+fakta|fact\s*check)\b", full_text, re.I):
        # The returned item is a report about checking a claim. If its exact
        # verdict could not be parsed, do not let claim keywords turn the
        # report itself into a hoax label or claim that the claim is true.
        label = "fakta"
        confidence = 0.45
        basis = "laporan cek fakta ditemukan; putusan klaim belum terbaca, label hanya untuk artikelnya"
    elif is_unsourced_criticism(full_text):
        # A publisher link proves where the text came from, not the truth of
        # unsupported accusations. Label only the observable negative tone.
        label = "negatif"
        confidence = max(0.78, float(prediction["probabilitas"]))
        basis = "prediksi sementara sentimen negatif; isi didominasi kritik tanpa rujukan data yang jelas, label ini tidak membuktikan tuduhan"
    elif prediction["label"] == "negatif" and prediction["probabilitas"] >= 0.78:
        label, confidence, basis = "negatif", prediction["probabilitas"], "indikator provokasi/sentimen pada isi"
    else:
        # Publication by a reputable outlet supports the report's provenance;
        # it does not, by itself, settle every claim in that report.
        label, confidence, basis = "fakta", max(0.55, prediction["probabilitas"]), "laporan dari penerbit tepercaya; bukan putusan klaim independen"

    return {
        "label": label,
        "probabilitas": round(float(confidence), 4),
        "basis": basis,
        "status": "terverifikasi" if verdict and (is_fact_check(domain) or is_official_primary_domain(domain)) else "prediksi_sementara",
        "putusan_eksplisit": basis == "putusan klaim yang dinyatakan eksplisit oleh sumber tepercaya",
    }


def search(query, social_posts=None):
    original_query = query.strip()
    query = compact_search_query(original_query)
    variants = expand_query(query)
    # Search the original claim, semantic rewrites, and varied confirmation or
    # rebuttal phrasing. The returned article body and source agreement decide
    # the outcome; a fact-check keyword is not required.
    feed_terms = list(dict.fromkeys([
        query,
        *variants[2:5],
        f"klarifikasi {query}",
        f"{query} dibantah",
        f"{query} benar",
    ]))[:7]
    # Bing News RSS exposes each publisher's direct URL in its apiclick link;
    # the UI therefore opens the actual article, never a Google search page.
    candidates = []
    seen_feed_links = set()

    def fetch_feed(term):
        url = f"https://www.bing.com/news/search?q={quote_plus(term)}&format=rss&mkt=id-ID"
        try:
            response = requests.get(url, timeout=(2, 3), headers={"User-Agent": "Mozilla/5.0 GeoSentry/1.0"})
            response.raise_for_status()
            return feedparser.parse(response.content).entries[:20]
        except requests.RequestException:
            return []

    feed_candidates = []
    with ThreadPoolExecutor(max_workers=min(8, len(feed_terms))) as executor:
        feeds = [executor.submit(fetch_feed, term) for term in feed_terms]
        for feed_future in as_completed(feeds):
            try:
                entries = feed_future.result()
            except Exception:
                continue
            for entry in entries[:8]:
                link = entry.get("link", "")
                parsed_link = urlparse(link)
                direct_url = parse_qs(parsed_link.query).get("url", [""])[0]
                if direct_url:
                    link = unquote(direct_url)
                if not link or link in seen_feed_links or not is_trusted(source_domain(link)):
                    continue
                seen_feed_links.add(link)
                feed_candidates.append({
                    "feed_title": re.sub(r"\s+-\s+[^-]+$", "", entry.get("title", "")).strip(),
                    "feed_link": link,
                    "feed_source": entry.get("source", {}).get("title", ""),
                    "published": entry.get("published", ""),
                    "summary": BeautifulSoup(entry.get("summary", ""), "html.parser").get_text(" ", strip=True),
                })
    # Rank by how well articles match the claim across all trusted publishers.
    # Do not reserve the result set for a particular source category.
    feed_candidates.sort(key=lambda item: (
        -relevance(query, item["feed_title"], item["summary"]),
        item["published"],
    ))
    candidates = feed_candidates[:8]

    articles, seen = [], set()
    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {executor.submit(article_text, item["feed_link"]): item for item in candidates}
        for future in as_completed(futures):
            item = futures[future]
            content = future.result()
            if not content:
                continue
            domain = source_domain(content["url"])
            if not is_trusted(domain) or content["url"] in seen:
                continue
            seen.add(content["url"])
            title = content["title"] or item["feed_title"]
            body = content["body"]
            title_score, body_score = relevance_components(query, title, body)
            score = round(min(1.0, title_score * 0.55 + body_score * 0.45), 3)
            # A keyword appearing once in a long unrelated article is not enough.
            # Require a meaningful title match or strong coverage in the body.
            min_score = 0.20 if is_fact_check(domain) else 0.28
            min_title = 0.25 if is_fact_check(domain) else 0.34
            min_body = 0.40 if is_fact_check(domain) else 0.50
            if score < min_score or (title_score < min_title and body_score < min_body):
                continue
            analysis = classify_article({"title": title, "body": body}, domain)
            articles.append({
                "title": title,
                "source": content["source"] or item["feed_source"] or domain,
                "domain": domain,
                "url": content["url"],
                "published": content["published"] or item["published"],
                "content": body,
                "content_length": len(body),
                "relevance": score,
                "title_relevance": round(title_score, 3),
                "body_relevance": round(body_score, 3),
                "analysis": analysis,
            })

    relevant_explicit_verdicts = [
        item for item in articles
        if item["relevance"] >= 0.72
        and item.get("title_relevance", 0) >= 0.50
        and item["analysis"].get("putusan_eksplisit")
    ]
    explicit_verdict_labels = {
        item["analysis"]["label"] for item in relevant_explicit_verdicts
        if item["analysis"].get("label") in {"fakta", "hoaks"}
    }
    verdict_conflict = len(explicit_verdict_labels) > 1
    explicit_verdict_source = None
    verdict_is_verified = False
    if len(explicit_verdict_labels) == 1:
        verdict_label = next(iter(explicit_verdict_labels))
        supporting_verdicts = [
            item for item in relevant_explicit_verdicts
            if item["analysis"].get("label") == verdict_label
        ]
        explicit_verdict_source = max(supporting_verdicts, key=lambda item: item["relevance"])
        corroborating_groups = independent_publisher_groups(supporting_verdicts)
        verdict_is_verified = (
            any(is_fact_check(item["domain"]) or is_official_primary_domain(item["domain"])
                for item in supporting_verdicts)
            or len(corroborating_groups) >= 2
        )
        for item in supporting_verdicts:
            item["analysis"]["status"] = "terverifikasi" if verdict_is_verified else "prediksi_sementara"

    social_candidates = []
    for post in social_posts or []:
        title = post.get("judul") or post.get("teks", "")[:150]
        body = post.get("teks", "")
        title_score, body_score = relevance_components(query, title, body, stem_body=False)
        score = round(min(1.0, title_score * 0.55 + body_score * 0.45), 3)
        if score < 0.30 or (title_score < 0.34 and body_score < 0.50) or not post.get("url"):
            continue
        social_candidates.append((score, post, title, body, title_score, body_score))

    # Rank all matching posts first, then run the slower local classifier only
    # on the handful that will actually be shown in search results.
    social_candidates.sort(key=lambda item: (item[0], str(item[1].get("dibuat_pada", ""))), reverse=True)
    for score, post, title, body, title_score, body_score in social_candidates[:5]:
        domain = source_domain(post["url"])
        analysis = predict_threat(body)
        if explicit_verdict_source:
            analysis["label"] = explicit_verdict_source["analysis"]["label"]
            analysis["status"] = "terverifikasi" if verdict_is_verified else "prediksi_sementara"
            analysis["probabilitas"] = explicit_verdict_source["analysis"]["probabilitas"]
            if not verdict_is_verified:
                analysis["probabilitas"] = min(float(analysis["probabilitas"]), 0.68)
            analysis["basis"] = f"putusan klaim eksplisit yang relevan: {explicit_verdict_source['source']}"
        elif analysis["label"] == "negatif":
            analysis["basis"] = "indikator sentimen/provokasi dalam isi; bukan bukti akun buzzer atau berbayar"
        else:
            # Social posts remain searchable, but a text-only model score is
            # deliberately capped when no matching external verdict exists.
            analysis["probabilitas"] = min(float(analysis["probabilitas"]), 0.58)
            analysis["basis"] = "prediksi teks media sosial; belum ada putusan klaim yang cocok"
        articles.append({
            "title": title,
            "source": post.get("sumber", "media sosial"),
            "domain": domain,
            "url": post["url"],
            "published": post.get("dibuat_pada", ""),
            "content": body,
            "content_length": len(body),
            "relevance": score,
            "title_relevance": round(title_score, 3),
            "body_relevance": round(body_score, 3),
            "analysis": {"label": analysis["label"], "probabilitas": analysis["probabilitas"], "basis": analysis["basis"], "status": analysis.get("status", "prediksi_sementara"), "putusan_eksplisit": False},
            "content_type": "postingan",
        })

    articles.sort(key=lambda item: (item["relevance"], item["published"]), reverse=True)
    if explicit_verdict_source and explicit_verdict_source not in articles[:10]:
        articles = articles[:9] + [explicit_verdict_source]
        articles.sort(key=lambda item: (item["relevance"], item["published"]), reverse=True)
    else:
        articles = articles[:10]
    publisher_articles = [item for item in articles if item.get("content_type") != "postingan"]
    social_articles = [item for item in articles if item.get("content_type") == "postingan"]
    if verdict_conflict:
        fallback = predict_threat(query)
        summary_label = fallback["label"]
        summary_confidence = min(float(fallback["probabilitas"]), 0.45)
        summary_status = "prediksi_sementara"
        summary_basis = "sumber tepercaya memberi putusan yang bertentangan; prediksi AI sementara ditampilkan dengan keyakinan rendah"
    elif relevant_explicit_verdicts and explicit_verdict_source:
        top = explicit_verdict_source
        summary_label = top["analysis"]["label"]
        if verdict_is_verified:
            summary_confidence = top["analysis"]["probabilitas"]
            summary_status = "terverifikasi"
            summary_basis = "putusan klaim didukung sumber resmi/pemeriksa fakta atau beberapa penerbit independen"
        else:
            summary_confidence = min(float(top["analysis"]["probabilitas"]), 0.68)
            summary_status = "prediksi_sementara"
            summary_basis = "putusan eksplisit ditemukan pada satu penerbit tepercaya; prediksi sementara menunggu sumber independen"
    elif articles and is_unsourced_criticism(f"{articles[0]['title']} {articles[0]['content']}"):
        top = articles[0]
        summary_label = "negatif"
        summary_confidence = min(float(top["analysis"]["probabilitas"]), 0.84)
        summary_status = "prediksi_sementara"
        summary_basis = (
            f"prediksi sementara sentimen negatif dari isi {top['source']} yang didominasi kritik tanpa rujukan data yang jelas; "
            "label ini tidak membuktikan tuduhan"
        )
    elif sum(1 for item in social_articles if item["analysis"]["label"] == "negatif" and item["analysis"]["probabilitas"] >= 0.78) >= 2:
        summary_label = "negatif"
        summary_confidence = min(0.82, max(item["analysis"]["probabilitas"] for item in social_articles if item["analysis"]["label"] == "negatif"))
        summary_status = "terverifikasi"
        summary_basis = "beberapa postingan cocok menunjukkan sentimen/provokasi negatif; ini tidak membuktikan buzzer"
    elif publisher_articles:
        matching_reports = [
            item for item in publisher_articles
            if not is_fact_check(item["domain"])
            and item["relevance"] >= 0.42 and (
                item.get("title_relevance", 0) >= 0.30
                or item.get("body_relevance", 0) >= 0.55
            )
        ]
        independent_publishers = independent_publisher_groups(matching_reports)
        matching_official = [
            item for item in matching_reports
            if is_official_primary_domain(item["domain"])
            and item["relevance"] >= 0.62
            and item.get("title_relevance", 0) >= 0.45
            and item.get("body_relevance", 0) >= 0.60
        ]
        if matching_official:
            top = max(matching_official, key=lambda item: item["relevance"])
            summary_label = "fakta"
            summary_confidence = min(float(top["analysis"]["probabilitas"]), 0.78)
            summary_status = "terverifikasi"
            summary_basis = f"laporan cocok dengan sumber primer resmi {top['source']}"
        elif independent_publishers:
            top = max(matching_reports, key=lambda item: item["relevance"])
            summary_label = "fakta"
            # Corroborating independent reporting should raise a provisional
            # fact prediction above the low-confidence text-only fallback.
            # Keep it provisional because reporting agreement is not an
            # explicit fact-check ruling.
            summary_confidence = min(0.70, 0.56 + 0.02 * min(len(independent_publishers), 7))
            summary_status = "prediksi_sementara"
            if len(independent_publishers) >= 2:
                summary_basis = f"prediksi fakta sementara; laporan cocok dari {len(independent_publishers)} penerbit berbeda, belum ada putusan eksplisit yang cocok"
            else:
                summary_basis = "prediksi fakta sementara; satu laporan penerbit cocok, belum ada putusan eksplisit yang cocok"
        else:
            # A single related article establishes that a report exists, not
            # that the underlying claim is true. Fall back to a visibly weaker
            # text prediction until an explicit verdict or corroboration exists.
            fallback = predict_threat(query)
            summary_label = fallback["label"]
            summary_confidence = min(float(fallback["probabilitas"]), 0.49)
            summary_status = "prediksi_sementara"
            summary_basis = "prediksi teks; sumber terkait ditemukan tetapi belum cukup untuk memastikan klaim"
    elif social_articles:
        top = social_articles[0]
        summary_label = top["analysis"]["label"]
        summary_confidence = min(float(top["analysis"]["probabilitas"]), 0.58)
        summary_status = "prediksi_sementara"
        summary_basis = top["analysis"]["basis"]
    else:
        fallback = predict_threat(query)
        summary_label = fallback["label"]
        is_criticism = is_unsourced_criticism(original_query)
        if is_criticism:
            summary_label = "negatif"
            summary_confidence = 0.84
        else:
            summary_confidence = min(float(fallback["probabilitas"]), 0.49)
        summary_status = "prediksi_sementara"
        summary_basis = (
            "prediksi sementara sentimen negatif; teks hanya memuat kritik/tuduhan tanpa rujukan data yang jelas dan label ini tidak membuktikan tuduhan"
            if is_criticism and summary_label == "negatif"
            else "prediksi teks saja; tidak ditemukan sumber terkait"
        )

    return {
        "success": True,
        "query": original_query,
        "matched_query": query,
        "expanded_queries": variants,
        "count": len(articles),
        "summary_label": summary_label,
        "summary_confidence": round(float(summary_confidence), 4),
        "summary_status": summary_status,
        "summary_basis": summary_basis,
        "articles": articles,
        "message": "Berita dibaca dari isi lengkap dan setiap hasil menaut ke penerbit aslinya." if articles else "Belum ditemukan berita relevan dari penerbit dan pemeriksa fakta yang didukung.",
    }


if __name__ == "__main__":
    try:
        if len(sys.argv) < 2 or not sys.argv[1].strip():
            raise ValueError("Kata kunci pencarian kosong")
        social_payload = sys.argv[2] if len(sys.argv) > 2 else sys.stdin.read()
        social_posts = json.loads(social_payload) if social_payload.strip() else []
        print(json.dumps(search(sys.argv[1].strip(), social_posts), ensure_ascii=False))
    except Exception as error:
        print(json.dumps({"success": False, "error": str(error)}, ensure_ascii=False))
        sys.exit(1)
