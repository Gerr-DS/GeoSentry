"""Evidence gate for records that are allowed into the public map."""

import re
import os
from urllib.parse import urlparse

from scripts.search_news import (
    article_text,
    compact_search_query,
    explicit_fact_check_verdict,
    is_fact_check,
    independent_publisher_groups,
    is_trusted,
    relevance_components,
)
from scripts.nlp.predict import predict_threat

SOCIAL_CLAIM_SEARCH_LIMIT = max(1, int(os.getenv("GEOSENTRY_SOCIAL_CLAIM_SEARCH_LIMIT", "140")))
_social_claim_searches = 0
_social_claim_cache = {}
_social_verification_metrics = {
    "checked": 0,
    "invalid_original_or_url": 0,
    "predicted_fakta": 0,
    "predicted_hoaks": 0,
    "predicted_negatif": 0,
    "verified_negatif": 0,
    "verified_by_source": 0,
    "verified_by_fact_check": 0,
    "verified_by_official_source": 0,
    "verified_by_news_portal": 0,
    "direct_article_links_checked": 0,
    "provisional_prediction": 0,
    "held_no_supported_label": 0,
    "claim_searches": 0,
    "claim_search_no_match": 0,
    "claim_search_limit_reached": 0,
    "conflicting_explicit_verdicts": 0,
    "fallback_hoaks_no_source": 0,
    "negative_call_to_action": 0,
}


def social_verification_metrics():
    return dict(_social_verification_metrics)

FACT_CHECK_DOMAINS = (
    "turnbackhoax.id", "cekfakta.com", "cekfakta.tempo.co", "mafindo.or.id",
)
NEGATIVE_ACTION_RE = re.compile(
    r"\b(?:menyerang|serang(?:lah)?|menyerbu|serbu(?:lah)?|membunuh|bunuh(?:lah)?|"
    r"membakar|bakar(?:lah)?|menghancurkan|hancurkan|merusak|rusak(?:kan)?|"
    r"menganiaya|aniaya(?:lah)?|memukul|pukul(?:lah)?|menembak|tembak(?:lah)?|"
    r"meledakkan|ledakkan|menculik|culik(?:lah)?|memersekusi|persekusi(?:lah)?|"
    r"menyabotase|sabotase(?:lah)?|mengintimidasi|intimidasi(?:lah)?|"
    r"mengancam|ancam(?:lah)?|dox(?:x|ing)|menyebarkan\s+(?:hoaks|fitnah)|"
    r"sebar(?:kan)?\s+(?:hoaks|fitnah)|fitnah(?:lah)?|memprovokasi|provokasi(?:lah)?)\b",
    re.IGNORECASE,
)
NEGATIVE_CTA_RE = re.compile(
    r"\b(?:ayo(?:lah)?|mari(?:lah)?|silakan|tolong|ajak(?:lah)?|mengajak|"
    r"serukan(?:lah)?|seruan(?:\s+untuk)?|kita\s+(?:harus|wajib)|harus|wajib|"
    r"segera|let['’]s|go\s+(?:and\s+)?|call\s+(?:for|on)|urge|"
    r"we\s+(?:should|must)|you\s+(?:should|must)|must|should)\b",
    re.IGNORECASE,
)
NEGATION_BEFORE_ACTION_RE = re.compile(
    r"\b(?:jangan|tidak\s+boleh|tak\s+boleh|dilarang|stop|hentikan|"
    r"menolak|mencegah|hindari|don't|do\s+not|never)\b",
    re.IGNORECASE,
)
REPORTED_CALL_RE = re.compile(
    r"\b(?:menurut|dilaporkan|diberitakan|mengutip|mengatakan|menyebut|"
    r"menuduh|diduga|dituduh|laporan|berita)\b.{0,70}\b"
    r"(?:mengajak|menyerukan|menyuruh|menyarankan|seruan)\b",
    re.IGNORECASE,
)

SOCIAL_HOSTS = {
    "twitter": ("x.com", "twitter.com"),
    "tiktok": ("tiktok.com",),
    "instagram": ("instagram.com",),
    "mastodon": (),
    "bluesky": ("bsky.app",),
    "reddit": ("reddit.com",),
}
NON_NEWS_PUBLISHER_DOMAINS = (
    "google.com", "bing.com", "facebook.com", "instagram.com", "tiktok.com",
    "x.com", "twitter.com", "reddit.com", "bsky.app", "youtube.com",
    "kompasiana.com",
)


def source_domain(url):
    return (urlparse(url or "").hostname or "").lower().removeprefix("www.")


def is_domain(domain, suffix):
    return domain == suffix or domain.endswith("." + suffix)


def is_fact_checker(domain):
    return any(is_domain(domain, item) for item in FACT_CHECK_DOMAINS)


def is_official_primary_domain(domain):
    return domain.endswith(".go.id") or domain.endswith(".ac.id") or domain.endswith(".gov") or domain.endswith(".gov.id")


def is_identifiable_news_publisher(url):
    """Return whether RSS identifies a publisher site, not an aggregator/social network."""
    domain = source_domain(url)
    return bool(domain and "." in domain and not any(is_domain(domain, item) for item in NON_NEWS_PUBLISHER_DOMAINS))


def has_negative_call_to_action(text):
    """Detect explicit calls for harmful conduct, not criticism or negative tone alone."""
    for sentence in re.split(r"(?<=[.!?;])\s+|[\r\n]+", str(text or "")):
        action = NEGATIVE_ACTION_RE.search(sentence)
        if not action:
            continue
        prefix = sentence[:action.start()]
        if NEGATION_BEFORE_ACTION_RE.search(prefix[-100:]):
            continue
        if REPORTED_CALL_RE.search(prefix) and not re.search(
            r"\b(?:ayo|ayolah|mari|marilah|let['’]s|kamu|anda)\b", prefix, re.IGNORECASE
        ):
            continue
        if NEGATIVE_CTA_RE.search(prefix):
            return True
        if not prefix.strip():
            return True
    return False


def unverified_source_fallback(title, text, reason):
    """Keep readable held items visible as explicitly provisional predictions."""
    combined = " ".join(part.strip() for part in (title or "", text or "") if part and part.strip())
    if not combined:
        return None
    if has_negative_call_to_action(combined):
        return {
            "label": "negatif",
            "probabilitas": 0.68,
            "status": "prediksi_sementara",
            "basis": f"sumber belum dapat dipastikan; teks memuat ajakan eksplisit ke tindakan merugikan ({reason}); label sentimen, bukan putusan benar/salah klaim",
        }
    return {
        "label": "hoaks",
        "probabilitas": 0.55,
        "status": "prediksi_sementara",
        "basis": f"sumber atau bukti pendukung belum dapat dipastikan ({reason}); ditampilkan sebagai prediksi hoaks sementara, bukan putusan final bahwa klaim terbukti palsu",
    }


def verify_news_candidate(title, text, url, *, publisher_url=None, publisher_name=None):
    """Check the original article, then classify readable unsupported items provisionally."""
    domain = source_domain(url)
    if domain in {"news.google.com", "google.com"}:
        publisher_domain = source_domain(publisher_url)
        if publisher_domain and is_identifiable_news_publisher(publisher_url):
            publisher_verdict = explicit_fact_check_verdict(title or "", text or "", publisher_domain)
            if is_fact_check(publisher_domain) and publisher_verdict in {"fakta", "hoaks"}:
                return {
                    "label": publisher_verdict,
                    "probabilitas": 0.86,
                    "status": "terverifikasi",
                    "basis": f"putusan eksplisit pemeriksa fakta {publisher_domain} tercantum di metadata RSS; halaman langsung belum terbaca",
                }
            if has_negative_call_to_action(f"{title or ''} {text or ''}"):
                return unverified_source_fallback(
                    title, text,
                    f"Google News mencatat penerbit {publisher_name or publisher_domain}, tetapi artikel asli tidak dapat dibuka",
                )
            return {
                "label": "fakta",
                "probabilitas": 0.58 if (is_official_primary_domain(publisher_domain) or is_trusted(publisher_domain)) else 0.52,
                "status": "prediksi_sementara",
                "basis": (
                    f"Google News mencatat penerbit {publisher_name or publisher_domain} ({publisher_domain}); "
                    "artikel langsung belum terbaca dan belum ada putusan cek fakta, jadi ini fakta sementara"
                ),
            }
        return unverified_source_fallback(title, text, "tautan agregator tidak mengungkap penerbit tepercaya")
    if not url or domain == "bing.com":
        return unverified_source_fallback(title, text, "tautan ke sumber asli tidak tersedia")

    feed_verdict = explicit_fact_check_verdict(title or "", text or "", domain)
    source_article = article_text(url)
    if not source_article:
        # Keep an explicit verdict from a dedicated fact-check feed usable if
        # that publisher blocks article-body retrieval; never do this for a
        # general news headline.
        if is_fact_check(domain) and feed_verdict in {"hoaks", "fakta"}:
            return {
                "label": feed_verdict,
                "probabilitas": 0.86,
                "status": "terverifikasi",
                "basis": f"putusan eksplisit pada RSS pemeriksa fakta {domain}; halaman artikel tidak dapat dibaca",
            }
        if is_official_primary_domain(domain) or (is_trusted(domain) and not is_fact_check(domain)):
            if has_negative_call_to_action(f"{title or ''} {text or ''}"):
                return unverified_source_fallback(title, text, f"penerbit tepercaya {domain} dikenal, tetapi halaman asli gagal dibaca")
            return {
                "label": "fakta",
                "probabilitas": 0.58,
                "status": "prediksi_sementara",
                "basis": f"tautan berasal dari penerbit tepercaya {domain}, tetapi halaman asli tidak terbaca; fakta sementara berdasarkan judul dan ringkasan feed",
            }
        return unverified_source_fallback(title, text, "halaman sumber asli tidak dapat dibaca")

    domain = source_domain(source_article.get("url") or url)
    if not (is_trusted(domain) or is_official_primary_domain(domain)):
        return unverified_source_fallback(title, text, f"penerbit {domain or 'tidak dikenal'} belum memenuhi daftar sumber tepercaya")
    article_title = source_article.get("title") or title or ""
    article_body = source_article.get("body") or text or ""
    title_match, body_match = relevance_components(
        title or text or "", article_title, article_body
    )
    if title_match < 0.25 and body_match < 0.45:
        return unverified_source_fallback(title, text, "isi sumber tidak cukup cocok dengan judul atau ringkasan")

    verdict = explicit_fact_check_verdict(article_title, article_body, domain)
    if verdict in {"hoaks", "fakta"}:
        authoritative_verdict = is_fact_check(domain) or is_official_primary_domain(domain)
        return {
            "label": verdict,
            "probabilitas": 0.90 if authoritative_verdict else 0.68,
            "status": "terverifikasi" if authoritative_verdict else "prediksi_sementara",
            "basis": (
                f"putusan eksplisit pada isi artikel sumber tepercaya {domain}"
                if authoritative_verdict
                else f"putusan eksplisit ditemukan pada satu portal {domain}; prediksi sementara menunggu sumber independen"
            ),
        }

    if is_fact_check(domain):
        return unverified_source_fallback(title, text, "putusan cek fakta eksplisit tidak ditemukan")

    if is_official_primary_domain(domain):
        if title_match < 0.50 and body_match < 0.70:
            return unverified_source_fallback(title, text, "isi artikel resmi tidak cukup cocok dengan klaim")
        return {
            "label": "fakta",
            "probabilitas": 0.78,
            "status": "terverifikasi",
            "basis": f"laporan langsung dari sumber primer resmi {domain}",
        }

    # Accept the report when its original page is readable and the RSS
    # headline substantially matches. A publisher report supports a temporary
    # fact prediction, not a verified claim; a hoax still needs an explicit
    # fact-check verdict.
    if is_trusted(domain) and not is_fact_check(domain):
        if title_match < 0.30 and body_match < 0.55:
            return unverified_source_fallback(title, text, "isi artikel tepercaya tidak cukup cocok dengan klaim")
        return {
            "label": "fakta",
            "probabilitas": 0.62,
            "status": "prediksi_sementara",
            "basis": f"fakta sementara berdasarkan kecocokan artikel asli dari penerbit tepercaya {domain}; belum ada putusan cek fakta independen",
        }
    return unverified_source_fallback(title, text, "tidak ditemukan bukti yang cukup dari sumber asli")


def is_direct_social_post(source, url):
    domain = source_domain(url)
    if not domain or any(part in url.lower() for part in ("/search?", "/explore/", "news.google.com", "bing.com")):
        return False
    if source == "mastodon":
        return "/@" in url and len(urlparse(url).path.strip("/").split("/")) >= 2
    if source == "bluesky":
        return is_domain(domain, "bsky.app") and "/profile/" in url and "/post/" in url
    if source == "reddit":
        return is_domain(domain, "reddit.com") and "/comments/" in url
    expected = SOCIAL_HOSTS.get(source, ())
    if not any(is_domain(domain, host) for host in expected):
        return False
    if source == "twitter":
        return "/status/" in url
    if source == "tiktok":
        return "/video/" in url
    if source == "instagram":
        return bool(re.search(r"/(?:p|reel|reels)/", url, re.I))
    return False


def verify_negative_post(source, text, url, *, original_content_fetched=False):
    """Label explicit harmful calls, not general criticism or negative tone."""
    if not original_content_fetched or not (text or "").strip():
        return None
    if not is_direct_social_post(source, url):
        return None
    if not has_negative_call_to_action(text):
        return None
    return {
        "label": "negatif",
        "probabilitas": 0.68,
        "status": "prediksi_sementara",
        "basis": f"postingan asli {source} memuat ajakan eksplisit ke tindakan merugikan; label sentimen sementara, bukan putusan benar/salah klaim",
    }


def _social_link_candidates(text, referenced_urls=None):
    """Collect direct trusted-news URLs embedded in a social post or its card."""
    text = re.sub(
        r"(https?://(?:www\.)?)\s+([\w.-]+\.[A-Za-z]{2,})",
        r"\1\2",
        str(text or ""),
        flags=re.I,
    )
    candidates = list(re.findall(r"https?://[^\s<>\"']+", text, flags=re.I))
    if isinstance(referenced_urls, str):
        referenced_urls = [referenced_urls]
    for item in referenced_urls or []:
        if isinstance(item, dict):
            item = item.get("url") or item.get("href")
        if item:
            candidates.append(str(item))

    unique = []
    seen = set()
    for candidate in candidates:
        candidate = candidate.strip().rstrip(".,!?;:)]}")
        domain = source_domain(candidate)
        if not candidate.startswith(("http://", "https://")):
            continue
        if not domain or not (is_trusted(domain) or is_official_primary_domain(domain)):
            continue
        if candidate in seen:
            continue
        seen.add(candidate)
        unique.append(candidate)
        if len(unique) >= 5:
            break
    return unique


def _direct_social_link_evidence(text, referenced_urls=None):
    """Read a linked article directly before relying on general news search."""
    urls = _social_link_candidates(text, referenced_urls)
    _social_verification_metrics["direct_article_links_checked"] += len(urls)
    if not urls:
        return None

    from scripts.search_news import source_domain as news_source_domain

    query = compact_search_query(text)
    candidates = []
    for url in urls:
        article = article_text(url)
        if not article:
            continue
        domain = news_source_domain(article.get("url") or url)
        if not (is_trusted(domain) or is_official_primary_domain(domain)):
            continue
        title = article.get("title", "")
        body = article.get("body", "")
        title_score, body_score = relevance_components(query, title, body)
        score = round(min(1.0, title_score * 0.55 + body_score * 0.45), 3)
        if score < 0.55 or (title_score < 0.40 and body_score < 0.65):
            continue

        verdict = explicit_fact_check_verdict(title, body, domain)
        if verdict in {"fakta", "hoaks"}:
            priority, label = 3, verdict
            basis = "putusan cek fakta eksplisit pada tautan berita yang disertakan postingan"
        elif is_official_primary_domain(domain):
            priority, label = 2, "fakta"
            basis = "isi postingan cocok dengan sumber primer resmi yang ditautkan"
        elif is_trusted(domain) and not is_fact_check(domain):
            priority, label = 1, "fakta"
            basis = "isi postingan cocok dengan artikel portal berita tepercaya yang ditautkan; artikel ini bukan putusan cek fakta independen"
        else:
            continue

        enriched_article = {
            **article,
            "url": article.get("url") or url,
            "source": article.get("source") or domain,
            "relevance": score,
            "title_relevance": round(title_score, 3),
            "body_relevance": round(body_score, 3),
            "analysis": {"probabilitas": 0.90 if priority == 3 else 0.78},
        }
        candidates.append((priority, score, enriched_article, label, basis))

    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    priority, _, article, label, basis = candidates[0]
    publisher_count = 0
    if priority in {1, 3}:
        publisher_count = len(independent_publisher_groups(
            [item[2] for item in candidates if item[0] == priority and item[3] == label]
        ))
        basis += f"; ditemukan {publisher_count} portal berbeda dengan putusan sama"
    return {
        "label": label,
        "article": article,
        "basis": basis,
        "priority": priority,
        "corroborating_publishers": publisher_count,
    }


def verify_social_post(source, text, url, *, original_content_fetched=False, referenced_urls=None):
    """Cross-check direct social posts with fact-checks, official sources and trusted news."""
    global _social_claim_searches
    _social_verification_metrics["checked"] += 1
    if not original_content_fetched or not (text or "").strip() or not is_direct_social_post(source, url):
        _social_verification_metrics["invalid_original_or_url"] += 1
        return None

    if has_negative_call_to_action(text):
        _social_verification_metrics["negative_call_to_action"] += 1
        _social_verification_metrics["provisional_prediction"] += 1
        return {
            "label": "negatif",
            "probabilitas": 0.68,
            "status": "prediksi_sementara",
            "basis": "postingan asli memuat ajakan eksplisit ke tindakan merugikan; label sentimen sementara, bukan putusan benar/salah klaim",
            "referensi": [],
        }

    prediction = predict_threat(text)
    prediction_metric = f"predicted_{prediction['label']}"
    if prediction_metric in _social_verification_metrics:
        _social_verification_metrics[prediction_metric] += 1
    evidence_conflict = False

    def provisional_prediction(reason=None):
        _social_verification_metrics["provisional_prediction"] += 1
        if has_negative_call_to_action(text):
            _social_verification_metrics["negative_call_to_action"] += 1
            return {
                "label": "negatif",
                "probabilitas": 0.68,
                "status": "prediksi_sementara",
                "basis": reason or "teks postingan memuat ajakan eksplisit ke tindakan merugikan; sentimen negatif sementara, bukan putusan benar/salah klaim",
                "referensi": [],
            }
        _social_verification_metrics["fallback_hoaks_no_source"] += 1
        return {
            "label": "hoaks",
            "probabilitas": 0.55,
            "status": "prediksi_sementara",
            "basis": reason or "sumber atau bukti pendukung tidak cukup; dikelompokkan sebagai prediksi hoaks sementara, bukan bukti final bahwa klaim palsu",
            "referensi": [],
        }

    # Cache repeated or near-identical posts so copies across accounts do not
    # consume the per-run news-search budget more than once.
    claim_key = re.sub(r"\s+", " ", text.casefold()).strip()
    missing = object()
    evidence = _social_claim_cache.get(claim_key, missing)
    if evidence is missing:
        evidence = _direct_social_link_evidence(text, referenced_urls)
        if evidence is not None:
            pass
        elif _social_claim_searches >= SOCIAL_CLAIM_SEARCH_LIMIT:
            _social_verification_metrics["claim_search_limit_reached"] += 1
            evidence = None
        else:
            # Search even posts the model calls negative. A negative tone does
            # not prove a factual claim is false, and the sentiment model can
            # mistake a factual report for negative content.
            _social_claim_searches += 1
            _social_verification_metrics["claim_searches"] += 1
            try:
                from scripts.search_news import (
                    is_fact_check as is_news_fact_check,
                    independent_publisher_groups,
                    is_trusted as is_trusted_news,
                    search,
                    source_domain as news_source_domain,
                )

                result = search(text)
                evidence_candidates = []
                for article in result.get("articles", []):
                    analysis = article.get("analysis") or {}
                    label = analysis.get("label")
                    relevance = float(article.get("relevance") or 0)
                    title_relevance = float(article.get("title_relevance") or 0)
                    body_relevance = float(article.get("body_relevance") or 0)
                    domain = news_source_domain(article.get("url", ""))
                    explicit_source_verdict = (
                        bool(analysis.get("putusan_eksplisit"))
                        and label in {"fakta", "hoaks"}
                        and relevance >= 0.68
                        and title_relevance >= 0.45
                    )
                    official_primary = (
                        is_official_primary_domain(domain)
                        and relevance >= 0.62
                        and title_relevance >= 0.45
                        and body_relevance >= 0.60
                    )
                    trusted_news = (
                        is_trusted_news(domain)
                        and not is_news_fact_check(domain)
                        and not is_official_primary_domain(domain)
                        and relevance >= 0.62
                        and title_relevance >= 0.45
                        and body_relevance >= 0.60
                    )
                    if explicit_source_verdict:
                        evidence_candidates.append((
                            3, relevance, article, label,
                            "putusan klaim eksplisit dari sumber tepercaya yang cocok",
                        ))
                    elif official_primary:
                        evidence_candidates.append((
                            2, relevance, article, "fakta",
                            "isi cocok dengan sumber primer resmi",
                        ))
                    elif trusted_news:
                        evidence_candidates.append((
                            1, relevance, article, "fakta",
                            "isi cocok dengan laporan portal berita tepercaya; laporan ini bukan putusan cek fakta independen",
                        ))

                explicit_labels = {
                    item[3] for item in evidence_candidates if item[0] == 3
                }
                if len(explicit_labels) > 1:
                    _social_verification_metrics["conflicting_explicit_verdicts"] += 1
                    evidence_conflict = True
                    evidence = None
                elif evidence_candidates:
                    evidence_candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
                    priority, _, article, label, basis = evidence_candidates[0]
                    corroborating_publishers = 0
                    if priority in {1, 3}:
                        independent_publishers = independent_publisher_groups(
                            [item[2] for item in evidence_candidates if item[0] == priority and item[3] == label]
                        )
                        corroborating_publishers = len(independent_publishers)
                        basis += f"; cocok dengan {corroborating_publishers} portal berbeda dengan putusan sama"
                    evidence = {
                        "label": label,
                        "article": article,
                        "basis": basis,
                        "priority": priority,
                        "corroborating_publishers": corroborating_publishers,
                    }
                else:
                    evidence = None
                    _social_verification_metrics["claim_search_no_match"] += 1
            except Exception:
                evidence = None
                _social_verification_metrics["claim_search_no_match"] += 1
        _social_claim_cache[claim_key] = evidence

    if not evidence:
        if evidence_conflict:
            return provisional_prediction("sumber tepercaya memberi putusan yang bertentangan; prediksi AI sementara ditampilkan dengan keyakinan rendah")
        return provisional_prediction()

    article = evidence["article"]
    priority = evidence["priority"]
    if priority == 3:
        if is_fact_check(source_domain(article.get("url", ""))):
            _social_verification_metrics["verified_by_fact_check"] += 1
        elif is_official_primary_domain(source_domain(article.get("url", ""))):
            _social_verification_metrics["verified_by_official_source"] += 1
        else:
            _social_verification_metrics["verified_by_news_portal"] += 1
    elif priority == 2:
        _social_verification_metrics["verified_by_official_source"] += 1
    else:
        _social_verification_metrics["verified_by_news_portal"] += 1
    _social_verification_metrics["verified_by_source"] += 1
    label = evidence["label"]
    if priority == 3:
        domain = source_domain(article.get("url", ""))
        authoritative = is_fact_check(domain) or is_official_primary_domain(domain)
        status = "terverifikasi" if authoritative or evidence["corroborating_publishers"] >= 2 else "prediksi_sementara"
        confidence = float(article["analysis"].get("probabilitas") or 0.90)
        if not authoritative and evidence["corroborating_publishers"] < 2:
            confidence = min(confidence, 0.68)
    else:
        confidence = min(0.70, max(0.58, float(article.get("relevance") or 0.58)))
        status = "prediksi_sementara" if priority == 1 else "terverifikasi"
        if priority == 1 and evidence["corroborating_publishers"] >= 2:
            confidence = min(0.74, confidence + 0.04)
        if priority == 1:
            evidence["basis"] = (
                "prediksi fakta sementara berdasarkan kecocokan klaim di "
                f"{evidence['corroborating_publishers']} penerbit berita independen; "
                "belum ada putusan eksplisit"
            )
    return {
        "label": label,
        "probabilitas": confidence,
        "status": status,
        "basis": f"{evidence['basis']}: {article.get('source') or article.get('domain')}; kecocokan {float(article.get('relevance') or 0):.0%}",
        "referensi": [{
            "sumber": article.get("source") or article.get("domain") or "sumber verifikasi",
            "url": article.get("url"),
            "judul": article.get("title") or "Sumber pemeriksaan klaim",
        }],
    }
