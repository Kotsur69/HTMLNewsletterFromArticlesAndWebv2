import json
import pdfplumber
import requests
import pandas as pd
import trafilatura

from jinja2 import Environment, FileSystemLoader
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlunparse
import time
import re
from datetime import datetime
import warnings
import contextlib
import sys
import os

@contextlib.contextmanager
def suppress_stderr():
    with open(os.devnull, "w") as devnull:
        old_stderr = sys.stderr
        sys.stderr = devnull
        try:
            yield
        finally:
            sys.stderr = old_stderr

warnings.filterwarnings("ignore")

# =====================
# FILTRY
# =====================

KEYWORDS = [
    "steel", "stal", "iron", "hut", "huta", "cbam", "co2", "carbon",
    "emissions", "production", "prices", "market", "news", "wydarzenia"
]

BLOCKED_URL_PATTERNS = [
    "/tag/", "/topics/", "/programme", "/shows", "/contact", "/about",
    "/terms", "/privacy", "/cookies", "/policy", "/legal", "/events",
    "x.com", "twitter.com", "youtube.com" , "cookie"
    "cookie",
    "cookies",
    "privacy",
    "polityka prywatności",
    "terms of use",
    "warunki korzystania",
    "dane osobowe",
    "consent",
    "zgodnie z przepisami ue",
    "rodo"
]

BLOCKED_CONTENT_PATTERNS = [
    "cookie",
    "cookies",
    "privacy",
    "polityka prywatności",
    "terms of use",
    "warunki korzystania",
    "dane osobowe",
    "consent",
    "zgodnie z przepisami ue",
    "rodo"
]


SOURCE_ICONS = {
    "euronews.com": "🟦",
    "worldsteel.org": "🏭",
    "steelonthenet.com": "⚙️",
    "gmk.center": "📊",
    "steelorbis.com": "🌍",

}
ABBREVIATIONS = {
    "r.", "tys.", "mln.", "mld.", "nr.", "itd.", "m.in.", "ok.", "proc."
}



DEBUG = True

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
                "max_tokens": 180,
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

def is_valid_article_url(url: str) -> bool:
    return not any(p in url.lower() for p in BLOCKED_URL_PATTERNS)

def normalize_url(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path.rstrip('/')
    normalized = urlunparse((parsed.scheme, parsed.netloc, path, '', '', ''))
    return normalized

def looks_like_article_url(url: str) -> bool:
    path = urlparse(url).path.lower()
    return len(path.split("/")) >= 3

def is_valid_summary(s: str) -> bool:
    return (
        len(s) >= 50 and
        len(s.split()) >= 8 and
        s.count(".") <= 1 and
        not re.search(r"\b20(2[0-5])\b", s)
    )

def limit_to_one_sentence(text: str) -> str:
    protected = text

    # zabezpiecz skróty
    for abbr in ABBREVIATIONS:
        protected = protected.replace(abbr, abbr.replace(".", "<DOT>"))

    parts = re.split(r'(?<=[.!?])\s+', protected.strip())
    first = parts[0]

    # przywróć kropki
    return first.replace("<DOT>", ".")


# =====================
# PDF / EXCEL
# =====================

def wczytaj_wszystkie_pdf(dir):
    out = ""
    for p in dir.glob("*.pdf"):
        if DEBUG:
            print(f"[PDF] Czytam: {p.name}")
        try:
            with suppress_stderr():
                with pdfplumber.open(p) as pdf:
                    for page in pdf.pages[:5]:
                        if page.extract_text():
                            out += page.extract_text() + "\n"
            if DEBUG:
                print(f"[PDF] OK: {p.name}")
        except Exception as e:
            if DEBUG:
                print(f"[PDF] BŁĄD: {p.name} → {e}")
    return out

def wczytaj_wszystkie_excel(dir):
    out = ""
    for x in dir.glob("*.xlsx"):
        if DEBUG:
            print(f"[XLSX] Czytam: {x.name}")
        try:
            df = pd.read_excel(x)
            out += df.head(5).to_string()
            if DEBUG:
                print(f"[XLSX] OK: {x.name} ({df.shape[0]} wierszy, {df.shape[1]} kolumn)")
        except Exception as e:
            if DEBUG:
                print(f"[XLSX] BŁĄD: {x.name} → {e}")
    return out

# =====================
# PODSUMOWANIE RAPORTÓW
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
# SCRAPER (TRAFILATURA)
# =====================

def pobierz_linki_artykulow(url):
    try:
        html = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10).text
    except:
        return []

    links = set()
    for m in re.findall(r'href=["\'](.*?)["\']', html):
        full = urljoin(url, m)
        if not is_valid_article_url(full):
            continue
        if any(k in full.lower() for k in KEYWORDS) or looks_like_article_url(full):
            links.add(full)
    return list(links)[:25]
def pobierz_tekst_i_date(url):
    downloaded = trafilatura.fetch_url(url)
    if not downloaded:
        return None, None

    metadata = trafilatura.metadata.extract_metadata(downloaded)
    if not metadata or not metadata.date:
        return None, None

    try:
        year = int(metadata.date[:4])
        if year < 2026:
            return None, None
    except:
        return None, None

    text = trafilatura.extract(
        downloaded,
        include_comments=False,
        include_tables=False
    )

    if not text or len(text) < 600:
        return None, None

    lower = text.lower()
    if any(p in lower for p in BLOCKED_CONTENT_PATTERNS):
        return None, None

    return text, metadata.date


def get_source_icon(url):
    domain = urlparse(url).netloc.lower()
    for k, v in SOURCE_ICONS.items():
        if k in domain:
            return v
    return "📰"

def scraper_news():
    newsy = []
    seen = set()
    seen_fp = set()

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
            text, date = pobierz_tekst_i_date(link)

            if DEBUG:
                print(
                    f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] "
                    f"{src['name']} | OK: {bool(text)}"
                )

            if not text:
                continue

            summary = limit_to_one_sentence(llm_call(prompt, text))

            if not is_valid_summary(summary):
                continue

            # fingerprint text
            fp = re.sub(r"\W+", "", summary.lower())[:300]
            if fp in seen_fp:
                continue
            seen_fp.add(fp)

            # normalized url
            norm_link = normalize_url(link)
            if norm_link in seen:
                continue
            seen.add(norm_link)

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
