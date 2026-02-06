import json
import pdfplumber
import requests
import pandas as pd
from bs4 import BeautifulSoup
from jinja2 import Environment, FileSystemLoader
from pathlib import Path
import warnings
from urllib.parse import urljoin, urlparse
import time
import re
from datetime import datetime, timedelta

warnings.filterwarnings("ignore")

# =====================
# FILTRY
# =====================

KEYWORDS = [
    "steel", "stal", "iron", "hut", "huta", "cbam", "co2", "carbon",
    "emissions", "arcelor", "worldsteel", "production", "prices", "market", "news", "wydarzenia"
]

BOILERPLATE_PATTERNS = [
    "cookie", "privacy", "pliki cookie", "youtube", "zaakceptuj", "odrzuć",
    "consent", "personaliz", "danych osobowych", "advertising",
    "enable javascript", "i'm an ai", "as an ai" "raport roczny", "podsumowanie roku",
   "analiza rynku", "white paper", "2020", "2021", "2022", "2023", "2024"

]

PROGRAM_BULLSHIT_PATTERNS = [
    "flagship morning", "tv show", "programme",
    "weekly show", "cotygodniowe spotkanie",
    "rozmowy polityczne", "euronews' flagship"
]

BLOCKED_URL_PATTERNS = [
    "/tag/", "/topics/", "/programme", "/shows", "/contact", "/about",
    "/terms", "/privacy", "/cookies", "/policy", "/legal", "/events",
    "worldsteel.org/global", "worldsteel.org/about",
    "steelorbis.com/", "x.com", "twitter.com", "youtube.com"
]

SOURCE_ICONS = {
    "euronews.com": "🟦",
    "worldsteel.org": "🏭",
    "steelonthenet.com": "⚙️",
    "gmk.center": "📊",
    "steelorbis.com": "🌍",
}

# =====================
# KONFIGURACJA
# =====================

BASE_DIR = Path(__file__).parent
KATALOG_PDF = BASE_DIR / "data/reports"
KATALOG_XLSX = BASE_DIR / "data/reports"
SCIEZKA_SOURCES = BASE_DIR / "data/sources.json"
KATALOG_SZABLONOW = BASE_DIR / "templates"
SCIEZKA_WYNIKOWA = BASE_DIR / "output/newsletter.html"

LM_STUDIO_URL = "http://localhost:1234"
MODEL_NAME = "qwen/qwen3-vl-8b"

MAX_DAYS_OLD = 9
DEBUG = True

# =====================
# LLM
# =====================

def llm_call(system_prompt, user_text, timeout=120, retries=2):
    prompt = f"""{system_prompt}

---
TEKST:
{user_text[:4000]}
"""
    for _ in range(retries):
        try:
            payload = {
                "model": MODEL_NAME,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.2,
                "max_tokens": 200,
                "top_p": 0.9
            }
            r = requests.post(
                f"{LM_STUDIO_URL}/v1/chat/completions",
                json=payload,
                timeout=timeout
            )
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"].strip()
        except:
            time.sleep(1)
    return ""

# =====================
# WALIDACJE
# =====================

def is_real_article_text(text: str) -> bool:
    if not text or len(text) < 200:
        return False
    lower = text.lower()
    banned = BOILERPLATE_PATTERNS + PROGRAM_BULLSHIT_PATTERNS
    return not any(b in lower for b in banned)
def looks_like_article_url(url: str) -> bool:
    path = urlparse(url).path.lower()
    return (
        len(path.split("/")) >= 3 and
        any(x in path for x in ["202", "news", "article", "market", "steel"])
    )


def is_valid_summary(s: str) -> bool:
    return (
        len(s) >= 40 and
        len(s.split()) >= 8 and
        s.count(".") <= 1 and
        not s.lower().endswith(("r.", "roku", "lat", "w"))
    )

def limit_to_one_sentence(text: str) -> str:
    sentences = re.split(r'(?<=[.!?])\s+', text.strip())
    return sentences[0]

def is_valid_article_url(url: str) -> bool:
    return not any(p in url.lower() for p in BLOCKED_URL_PATTERNS)

# =====================
# PDF / EXCEL
# =====================

def wyciagnij_tekst_pdf(path):
    text = ""
    try:
        with pdfplumber.open(path) as pdf:
            for p in pdf.pages[:10]:
                if p.extract_text():
                    text += p.extract_text() + "\n"
    except:
        pass
    return text

def wczytaj_wszystkie_pdf(dir):
    return "".join(wyciagnij_tekst_pdf(p) for p in dir.glob("*.pdf"))

def wczytaj_wszystkie_excel(dir):
    out = ""
    for x in dir.glob("*.xlsx"):
        try:
            df = pd.read_excel(x)
            out += df.head(5).to_string()
        except:
            pass
    return out

# =====================
# PODSUMOWANIE DOKUMENTÓW
# =====================

def podsumowanie_dokumentow():
    text = wczytaj_wszystkie_pdf(KATALOG_PDF) + wczytaj_wszystkie_excel(KATALOG_XLSX)
    if not text.strip():
        return {"streszczenie": "Brak raportów w tym wydaniu.", "kluczowe_wnioski": []}

    prompt = """
Jesteś redaktorem newslettera rynku stali.
- Dokładnie 2 pełne zdania
- Język: polski
- Styl: faktograficzny, executive
- Bez meta-tekstu
"""
    s = llm_call(prompt, text)
    sentences = re.split(r'(?<=[.!?])\s+', s)
    s = " ".join(sentences[:2])
    return {"streszczenie": s, "kluczowe_wnioski": [s]}

# =====================
# SCRAPER
# =====================

def extract_article_date(soup, url):
    time_tag = soup.find("time")
    if time_tag:
        dt = time_tag.get("datetime") or time_tag.get_text(strip=True)
        try:
            return datetime.fromisoformat(dt[:10])
        except:
            pass

    for meta_name in ["article:published_time", "pubdate", "date", "publish-date"]:
        meta = soup.find("meta", {"property": meta_name}) or soup.find("meta", {"name": meta_name})
        if meta and meta.get("content"):
            try:
                return datetime.fromisoformat(meta["content"][:10])
            except:
                pass

    m = re.search(r"/(20\d{2})/(\d{2})/(\d{2})/", url)
    if m:
        y, mth, d = map(int, m.groups())
        return datetime(y, mth, d)

    return None

def safe_get(url):
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        r.raise_for_status()
        return r.text
    except:
        return None

def pobierz_linki_artykulow(url):
    html = safe_get(url)
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    links = set()

    for a in soup.find_all("a", href=True):
        href = a["href"]
        full = urljoin(url, href)
        h = href.lower()
        t = a.get_text(strip=True).lower()

        if not is_valid_article_url(full):
            continue

        # 1️⃣ klasyczne keywordy
        if any(k in h or k in t for k in KEYWORDS):
            links.add(full)
            continue

        # 2️⃣ fallback – URL wygląda jak artykuł
        if looks_like_article_url(full):
            links.add(full)

    return list(links)[:30]


def pobierz_tekst(url):
    html = safe_get(url)
    if not html:
        return None
   
    soup = BeautifulSoup(html, "html.parser")

    # Próba wyciągnięcia daty
    pub_date = extract_article_date(soup, url)

    paragraphs = []
    for p in soup.find_all("p"):
        t = p.get_text(strip=True)
        # Odrzucamy zbyt krótkie fragmenty i boilerplate
        if len(t) > 80 and not any(b in t.lower() for b in BOILERPLATE_PATTERNS):
            paragraphs.append(t)

    text = " ".join(paragraphs[:30])[:5000]

    # Weryfikacja, czy tekst w ogóle nadaje się na artykuł
    if not is_real_article_text(text):
        return None

    # ❗ NOWA LOGIKA: fallback
    # Jeśli nie udało się znaleźć daty, ale tekst przeszedł walidację (is_real_article_text)
    # to przepuszczamy go dalej, zakładając, że może być istotny.
    if not pub_date:
        return text

    # Jeśli data została znaleziona, sprawdzamy czy artykuł nie jest za stary
    if datetime.now() - pub_date > timedelta(days=MAX_DAYS_OLD):
        return None

    return text

def get_source_icon(url):
    domain = urlparse(url).netloc.lower()
    for k, v in SOURCE_ICONS.items():
        if k in domain:
            return v
    return "📰"

def scraper_news():
    newsy = []
    seen = set()


    with open(SCIEZKA_SOURCES, encoding="utf-8") as f:
        sources = json.load(f)

    prompt = """
Jesteś redaktorem profesjonalnego newslettera branży stalowej.

Zadanie:
Napisz DOKŁADNIE JEDNO pełne zdanie, które w jasny i rzeczowy sposób informuje,
o czym jest artykuł, i jednocześnie zachęca do kliknięcia w link.

Zasady:
- Dokładnie 1 zdanie (bez drugiego, bez średników)
- Język: polski
- Styl: faktograficzny, executive, neutralny
- Opisz KONKRET: decyzję, trend, wydarzenie, dane lub zmianę na rynku stali
- NIE używaj meta-zwrotów typu „artykuł opisuje”
- Wyjście: wyłącznie gotowe zdanie newsletterowe
- Jeśli tekst dotyczy przeszłych lat (np. 2023, 2022) → NIE generuj zdania
- Oceń wydźwięk dla producenta stali: 1 (pozytywny), 0 (neutralny), -1 (negatywny).
"""

    for src in sources:
        for link in pobierz_linki_artykulow(src["url"]):
            text = pobierz_tekst(link)
            if DEBUG:
                print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Źródło: {src['name']} | Link: {link} | Tekst OK: {bool(text)}")

     
            if not text:
                continue


            summary = llm_call(prompt, text)
            summary = limit_to_one_sentence(summary)

            if not is_valid_summary(summary):
                continue

            fp = re.sub(r"\W+", "", summary.lower())[:320]
            if fp in seen:
                continue
            seen.add(fp)

            newsy.append({
                "tytul": src["name"],
                "podsumowanie": summary,
                "link": link,
                "icon": get_source_icon(link)
            })

            time.sleep(1)

    return newsy



# =====================
# HTML
# =====================

def generuj_email(dane):
    env = Environment(loader=FileSystemLoader(KATALOG_SZABLONOW))
    tpl = env.get_template("email_template.html")
    html = tpl.render(**dane)
    SCIEZKA_WYNIKOWA.parent.mkdir(parents=True, exist_ok=True)
    SCIEZKA_WYNIKOWA.write_text(html, encoding="utf-8")

# =====================
# MAIN
# =====================

def main():
    dokumenty = podsumowanie_dokumentow()
    newsy = scraper_news()
    generuj_email({
        "streszczenie": dokumenty["streszczenie"],
        "kluczowe_wnioski": dokumenty["kluczowe_wnioski"],
        "najwazniejsze_news": newsy
    })
    print("✅ Newsletter wygenerowany")

if __name__ == "__main__":
    main()
