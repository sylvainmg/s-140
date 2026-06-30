"""
Module : jw_org_weekly_scraper
Description : Extraction et structuration du programme hebdomadaire (Malagasy) depuis jw.org.
Dépendances : curl_cffi, beautifulsoup4, re, datetime
Usage : Orchestrer le scraping via get_week_urls() puis scrape_week(). Les données sont retournées sous forme de dictionnaires.
"""

import re
from bs4 import BeautifulSoup
from curl_cffi import requests
import curl_cffi.requests as req
import datetime as dt
from concurrent.futures import ThreadPoolExecutor, as_completed


# ── Configuration ──
# NOTE: OUTPUT_JSON a été supprimé car non référencé dans ce module (dead code).

BASE_URL = "https://www.jw.org"
# Pattern pour extraire la durée brute entre parenthèses (ex: "(10 min.)")
DURATION_RE = re.compile(r"\((\d+)\s*min\.?\)")


def resolve_url(url: str) -> str:
    """
    Résout les redirections HTTP et retourne l'URL finale absolue.

    Args:
        url: URL initiale (peut contenir des redirections ou des liens 'finder').

    Returns:
        str: URL finale après suivi des redirections.
    """
    # Session avec impersonation Chrome pour contourner les WAF/blocages de sécurité
    r = req.get(url, allow_redirects=True, impersonate="chrome", timeout=10)

    if r.redirect_url:
        return str(r.redirect_url)
    return str(r.url)


def find_duration(text: str) -> int | None:
    """
    Extrait la durée numérique d'une chaîne de caractères.

    Args:
        text: Chaîne contenant potentiellement une durée formatée.

    Returns:
        int | None: Durée en minutes si trouvée, sinon None.
    """
    m = DURATION_RE.search(text)
    return int(m.group(1)) if m else None


def strip_duration(text: str) -> str:
    """
    Supprime le pattern de durée d'une chaîne et nettoie les espaces superflus.

    Args:
        text: Chaîne contenant une durée.

    Returns:
        str: Chaîne nettoyée sans la durée.
    """
    return DURATION_RE.sub("", text).strip()


def clean_title(text: str) -> str:
    """
    Normalise et nettoie un titre de programme.

    - Supprime les espaces superflus.
    - Retire les suffixes techniques (ISAN, TSY ARA-POTOANA, etc.).
    - Standardise les séparateurs '|' en conservant un espace de chaque côté.

    Args:
        text: Titre brut extrait du DOM.

    Returns:
        str: Titre nettoyé et formaté.
    """
    text = " ".join(text.split()).strip()
    text = re.sub(
        r"\s*(ISAN-TRANO\.?.*|TSY ARA-POTOANA\.?.*|AMPAHIBEMASO\.?.*"
        r"|Fiaraha-midinika\.?|Toko\s+\d+.*|Mividiana\s+Saha.*"
        r"|toko\s+\d+.*|tokony halefa.*)$",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"\s*\|\s*", " | ", text)
    return text.strip()


def get_siblings_text(tag, limit: int = 6) -> list[str]:
    """
    Récupère le texte des frères suivants jusqu'à une balise de titre (h2/h3).

    Args:
        tag: Balise BeautifulSoup de départ.
        limit: Nombre maximum de frères à parcourir.

    Returns:
        list[str]: Liste des textes extraits.
    """
    texts = []
    for sib in tag.next_siblings:
        if getattr(sib, "name", None) in ("h2", "h3"):
            break
        raw = getattr(sib, "get_text", lambda **k: "")(separator=" ", strip=True)
        if raw:
            texts.append(raw)
        if len(texts) >= limit:
            break
    return texts


def get_week_urls(index_url: str) -> list[str]:
    """
    Récupère la page index mensuelle et en extrait les URLs des pages hebdomadaires.

    Args:
        index_url: URL de la page index mensuelle.

    Returns:
        list[str]: Liste des URLs absolues des semaines, sans doublons.
    """
    print(f"Fetching index: {index_url}")
    resp = requests.get(index_url, impersonate="chrome", timeout=15)
    if resp.status_code != 200:
        print(f"❌ HTTP {resp.status_code} on index")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    week_urls = []
    seen = set()

    # Les liens des semaines contiennent '/fivoriana-vj-tari-dalana/...-mwb/Fivoriana-'
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if re.search(
            r"/fivoriana-vj-tari-dalana/[^/]+-mwb/Fivoriana-", href, re.IGNORECASE
        ):
            full = BASE_URL + href if href.startswith("/") else href
            if full not in seen:
                seen.add(full)
                week_urls.append(full)

    print(f"Found {len(week_urls)} week(s).")
    return week_urls


def scrape_week(url: str) -> tuple[str, dict] | None:
    """
    Analyse une page hebdomadaire et retourne les données structurées.

    Args:
        url: URL de la page hebdomadaire.

    Returns:
        tuple[str, dict] | None: (week_key, {bible_reading, full_ordered_program}) ou None en cas d'erreur.
    """
    print(f"  Scraping: {url}")
    resp = requests.get(url, impersonate="chrome", timeout=15)
    if resp.status_code != 200:
        print(f"  ❌ HTTP {resp.status_code}")
        return None

    soup = BeautifulSoup(resp.text, "html.parser")
    article = soup.find("article") or soup.find(id="regionMain") or soup.body

    # ── 1. Lecture biblique ──
    bible_chapters = "N/A"
    for h2 in article.find_all("h2"):
        txt = " ".join(h2.get_text().split()).strip()
        if re.search(r"[A-Z]{3,}", txt) and len(txt) < 60:
            bible_chapters = txt
            break

    # ── 2. Clé de la semaine ──
    week_key = "?"
    h1 = soup.find("h1")
    if h1:
        h1_text = " ".join(h1.get_text().split()).strip()
        m = re.search(
            r"(\d+\s+\w+\s*[–\-—]\s*\d+\s+\w+|\d+\s*[-–]\s*\d+\s+\w+)", h1_text
        )
        if m:
            week_key = " ".join(m.group(1).split()).strip()

    # ── 3. Structuration du programme ──
    SECTION_MAP = [
        ("harena avy ao", "HARENA AVY AO AMIN'NY TENIN'ANDRIAMANITRA"),
        ("fampiofanana amin", "FAMPIOFANANA AMIN'NY FANOMPOANA"),
        ("ny fiainantsika kristianina", "NY FIAINANTSIKA KRISTIANINA"),
    ]

    ordered_schedule = []
    found_living = False
    seen_titles = set()

    for tag in article.find_all(["h2", "h3"]):
        raw = " ".join(tag.get_text(separator=" ").split()).strip()
        raw_lower = raw.lower()

        # Ignorer les h3 internes aux boîtes de contenu
        if tag.name == "h3" and tag.find_parent(class_="boxContent"):
            continue

        # Ignorer les balises de navigation/pied de page
        if any(kw in raw_lower for kw in ["tari-dalana", "loha hevitra", "hifidy fiteny", "hiverina", "manaraka"]):
            continue

        if tag.name == "h2":
            for kw, label in SECTION_MAP:
                if kw in raw_lower:
                    if label == "NY FIAINANTSIKA KRISTIANINA":
                        if found_living:
                            break
                        found_living = True
                    if (
                        not ordered_schedule
                        or ordered_schedule[-1].get("title") != label
                    ):
                        ordered_schedule.append(
                            {"type": "section", "title": label, "duration": None}
                        )
                    break
            continue

        # ── Traitement des éléments (h3) ──
        duration = find_duration(raw)
        title_raw = strip_duration(raw)

        # Fallback: chercher la durée dans les frères suivants
        if duration is None:
            for sib_text in get_siblings_text(tag, limit=6):
                duration = find_duration(sib_text)
                if duration is not None:
                    break

        title_lower = title_raw.lower()
        if "teny fampidirana" in title_lower and duration is None:
            duration = 1
        if "teny famaranana" in title_lower and duration is None:
            duration = 3

        is_song = (
            bool(re.search(r"\bhira\b", title_lower)) and "fianarana" not in title_lower
        )
        node_type = "song" if is_song else "part"

        # Gestion spécifique des chants hors section principale
        if node_type == "song" and not any(
            x in title_lower for x in ["vavaka", "teny fampidirana", "teny famaranana"]
        ):
            duration = None
            if not found_living and len(ordered_schedule) > 3:
                label = "NY FIAINANTSIKA KRISTIANINA"
                if not ordered_schedule or ordered_schedule[-1].get("title") != label:
                    ordered_schedule.append(
                        {"type": "section", "title": label, "duration": None}
                    )
                found_living = True

        title = clean_title(title_raw)

        # Simplification des titres de chants ouverts/fermés
        if node_type == "song" and duration is not None:
            hira_match = re.search(r"Hira\s+\d+", title)
            if hira_match:
                title = hira_match.group(0)

        if title in seen_titles:
            continue
        seen_titles.add(title)

        ordered_schedule.append(
            {
                "type": node_type,
                "title": title,
                "duration": duration,
            }
        )

    return week_key, {
        "bible_reading": bible_chapters,
        "full_ordered_program": ordered_schedule,
    }


def extract_month_label(url: str) -> str:
    """
    Extrait et formate le mois/année depuis une URL jw.org.

    Args:
        url: URL cible (peut contenir un finder/redirect).

    Returns:
        str: Label formaté (ex: 'Jolay-Aogositra 2026') ou fallback YYYYMM.
    """
    # 1. Résolution des URLs courtes/finder
    if "finder" in url:
        try:
            url = resolve_url(url)
        except Exception as e:
            print(f"⚠️ Échec de résolution de l'URL finder : {e}")

    # 2. Extraction via Regex sur le chemin final
    m = re.search(r"/([a-z0-9-]+)-mwb/", url, re.IGNORECASE)
    if m:
        try:
            raw_string = m.group(1)  # ex: 'jolay-aogositra-2026'

            # Extraction de l'année (4 derniers chiffres)
            year_match = re.search(r"(\d{4})$", raw_string)
            year = (
                year_match.group(1) if year_match else dt.datetime.now().strftime("%Y")
            )

            # Nettoyage de la partie mois
            just_months = re.sub(r"-\d{4}$", "", raw_string)

            # Capitalisation et formatage final
            formatted_months = "-".join(
                word.capitalize() for word in just_months.split("-")
            )
            return f"{formatted_months} {year}"

        except Exception as e:
            print(f"⚠️ Erreur formatage regex : {e}")

    # 3. Fallback
    return dt.datetime.now().strftime("%Y%m")

def scrape_all_weeks(index_url: str, max_workers: int = 6):
    week_urls = get_week_urls(index_url)
    results = []

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(scrape_week, url) for url in week_urls]

        for f in as_completed(futures):
            r = f.result()
            if r:
                results.append(r)

    return results