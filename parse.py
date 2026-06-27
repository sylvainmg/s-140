import json
import re
from bs4 import BeautifulSoup
from curl_cffi import requests
import datetime

OUTPUT_JSON = "real_workbook_program.json"

# ── URL de la page mensuelle à scraper ──
MONTHLY_INDEX_URL = "https://www.jw.org/mg/zavatra-misy/fivoriana-vj-tari-dalana/jolay-aogositra-2026-mwb/"

BASE_URL = "https://www.jw.org"
DURATION_RE = re.compile(r'\((\d+)\s*min\.?\)')


def find_duration(text):
    m = DURATION_RE.search(text)
    return int(m.group(1)) if m else None


def strip_duration(text):
    return DURATION_RE.sub('', text).strip()


def clean_title(text):
    text = " ".join(text.split()).strip()
    text = re.sub(
        r'\s*(ISAN-TRANO\.?.*|TSY ARA-POTOANA\.?.*|AMPAHIBEMASO\.?.*'
        r'|Fiaraha-midinika\.?|Toko\s+\d+.*|Mividiana\s+Saha.*'
        r'|toko\s+\d+.*|tokony halefa.*)$',
        '', text, flags=re.IGNORECASE
    )
    text = re.sub(r'\s*\|\s*', ' | ', text)
    return text.strip()


def get_siblings_text(tag, limit=6):
    texts = []
    for sib in tag.next_siblings:
        if getattr(sib, 'name', None) in ('h2', 'h3'):
            break
        raw = getattr(sib, 'get_text', lambda **k: '')(separator=' ', strip=True)
        if raw:
            texts.append(raw)
        if len(texts) >= limit:
            break
    return texts


def get_week_urls(index_url):
    """Fetch the monthly index page and extract all week article URLs."""
    print(f"Fetching index: {index_url}")
    resp = requests.get(index_url, impersonate="chrome", timeout=15)
    if resp.status_code != 200:
        print(f"❌ HTTP {resp.status_code} on index")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    week_urls = []
    seen = set()

    # Week links are <a href="/mg/zavatra-misy/fivoriana-vj-tari-dalana/...mwb/Fivoriana-...">
    for a in soup.find_all('a', href=True):
        href = a['href']
        # Must be a week page (contains "Fivoriana-Momba" or "Fandaharana") under the mwb path
        if re.search(r'/fivoriana-vj-tari-dalana/[^/]+-mwb/Fivoriana-', href, re.IGNORECASE):
            full = BASE_URL + href if href.startswith('/') else href
            if full not in seen:
                seen.add(full)
                week_urls.append(full)

    print(f"Found {len(week_urls)} week(s).")
    return week_urls


def scrape_week(url):
    """Scrape a single week page and return (week_key, data) or None on failure."""
    print(f"  Scraping: {url}")
    resp = requests.get(url, impersonate="chrome", timeout=15)
    if resp.status_code != 200:
        print(f"  ❌ HTTP {resp.status_code}")
        return None

    soup = BeautifulSoup(resp.text, "html.parser")
    article = soup.find('article') or soup.find(id='regionMain') or soup.body

    # ── Bible reading ──
    bible_chapters = "N/A"
    for h2 in article.find_all('h2'):
        txt = " ".join(h2.get_text().split()).strip()
        if re.search(r'[A-Z]{3,}', txt) and len(txt) < 60:
            bible_chapters = txt
            break

    # ── Week key ──
    week_key = "?"
    h1 = soup.find('h1')
    if h1:
        h1_text = " ".join(h1.get_text().split()).strip()
        m = re.search(r'(\d+\s+\w+\s*[–\-—]\s*\d+\s+\w+|\d+\s*[-–]\s*\d+\s+\w+)', h1_text)
        if m:
            week_key = " ".join(m.group(1).split()).strip()

    # ── Program ──
    SECTION_MAP = [
        ("harena avy ao",              "🟢 HARENA AVY AO AMIN'NY TENIN'ANDRIAMANITRA"),
        ("fampiofanana amin",           "🟡 FAMPIOFANANA AMIN'NY FANOMPOANA"),
        ("mieza hahasahy",              "🟡 FAMPIOFANANA AMIN'NY FANOMPOANA"),
        ("ny fiainantsika kristianina", "🔴 NY FIAINANTSIKA KRISTIANINA"),
    ]

    ordered_schedule = []
    found_living = False
    seen_titles = set()

    for tag in article.find_all(['h2', 'h3']):
        raw = " ".join(tag.get_text(separator=' ').split()).strip()
        raw_lower = raw.lower()

        if tag.name == 'h3':
            # Ignorer si le h3 est dans un bloc boxContent
            if tag.find_parent(class_='boxContent'):
                continue

        if any(x in raw_lower for x in ["tari-dalana", "loha hevitra", "hifidy fiteny", "hiverina", "manaraka"]):
            continue

        if tag.name == 'h2':
            for kw, label in SECTION_MAP:
                if kw in raw_lower:
                    if label == "🔴 NY FIAINANTSIKA KRISTIANINA":
                        if found_living:
                            break
                        found_living = True
                    if not ordered_schedule or ordered_schedule[-1].get('title') != label:
                        ordered_schedule.append({"type": "section", "title": label, "duration": None})
                    break
            continue

        # h3 — program part or song
        duration = find_duration(raw)
        title_raw = strip_duration(raw)

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

        is_song = bool(re.search(r'\bhira\b', title_lower)) and "fianarana" not in title_lower
        node_type = "song" if is_song else "part"

        if node_type == "song" and not any(x in title_lower for x in ["vavaka", "teny fampidirana", "teny famaranana"]):
            duration = None
            if not found_living and len(ordered_schedule) > 3:
                label = "🔴 NY FIAINANTSIKA KRISTIANINA"
                if not ordered_schedule or ordered_schedule[-1].get('title') != label:
                    ordered_schedule.append({"type": "section", "title": label, "duration": None})
                found_living = True

        title = clean_title(title_raw)

        # Extract just "Hira XX" for opening/closing songs
        if node_type == "song" and duration is not None:
            hira_match = re.search(r'Hira\s+\d+', title)
            if hira_match:
                title = hira_match.group(0)

        if title in seen_titles:
            continue
        seen_titles.add(title)

        ordered_schedule.append({
            "type": node_type,
            "title": title,
            "duration": duration,
        })

    return week_key, {
        "bible_reading": bible_chapters,
        "full_ordered_program": ordered_schedule,
    }

def extract_month_label(url: str) -> str:
    """Extrait 'Janoary-Febroary' depuis l'URL jw.org."""
    m = re.search(r'/([a-z]+-[a-z]+-\d{4})-mwb/', url, re.IGNORECASE)
    if m:
        parts = m.group(1).rsplit('-', 1)  # ['janoary-febroary', '2026']
        return parts[0].title()  # 'Janoary-Febroary'
    return datetime.now().strftime('%Y%m')

def main():
    week_urls = get_week_urls(MONTHLY_INDEX_URL)
    if not week_urls:
        print("No week URLs found. Exiting.")
        return

    all_data = {}
    for url in week_urls:
        result = scrape_week(url)
        if result:
            week_key, data = result
            all_data[week_key] = data

    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(all_data, f, ensure_ascii=False, indent=4)

    print(f"\n✨ Done! {len(all_data)} week(s) saved to '{OUTPUT_JSON}'")
    print(json.dumps(all_data, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()