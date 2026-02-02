import json
import pdfplumber
import requests
import pandas as pd
from bs4 import BeautifulSoup
from jinja2 import Environment, FileSystemLoader
from pathlib import Path
import warnings
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from concurrent.futures import ThreadPoolExecutor, as_completed

warnings.filterwarnings("ignore")

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
MODEL_NAME = "liquid/lfm2-1.2b"

# =====================
# LLM – STABILNE WYWOŁANIE
# =====================

def llm_call(prompt, text, timeout=300):
    payload = {
        "model": MODEL_NAME,
        "prompt": f"{prompt}\n\nTEKST:\n{text[:4000]}",
        "temperature": 0.0,
        "max_output_tokens": 600
    }

    try:
        r = requests.post(
            f"{LM_STUDIO_URL}/v1/completions",
            json=payload,
            timeout=timeout
        )
        r.raise_for_status()
        data = r.json()
        return data.get("completions", [{}])[0].get("content", "").strip()
    except requests.exceptions.ReadTimeout:
        print("⏱️ Timeout LLM – pomijam")
    except Exception as e:
        print(f"❌ Błąd LLM: {e}")

    return ""

# =====================
# PDF
# =====================

def wyciagnij_tekst_pdf(sciezka):
    tekst = ""
    with pdfplumber.open(sciezka) as pdf:
        for page in pdf.pages:
            if page.extract_text():
                tekst += page.extract_text() + "\n"
    return tekst

def wczytaj_wszystkie_pdf(katalog, limit=80_000):
    calosc = ""
    for pdf in katalog.glob("*.pdf"):
        print(f"   → PDF: {pdf.name}")
        tekst = wyciagnij_tekst_pdf(pdf)
        calosc += tekst[:limit]
    return calosc

# =====================
# EXCEL
# =====================

def streszczenie_excel(df):
    opis = []
    num = df.select_dtypes(include="number")
    for col in num.columns[:5]:
        opis.append(
            f"{col}: min={num[col].min()}, max={num[col].max()}, avg={round(num[col].mean(),2)}"
        )
    return "\n".join(opis)

def wczytaj_wszystkie_excel(katalog):
    calosc = ""
    for plik in katalog.glob("*.xlsx"):
        print(f"   → Excel: {plik.name}")
        try:
            df = pd.read_excel(plik)
            calosc += streszczenie_excel(df.head(300))
        except Exception as e:
            print(f"❌ Excel error {plik.name}: {e}")
    return calosc

# =====================
# PODSUMOWANIE PDF + EXCEL
# =====================

def podsumowanie_dokumentow():
    prompt = """
Przygotowujesz wewnętrzny newsletter zarządczy.

ZWRÓĆ WYŁĄCZNIE w tym formacie:

===STRESZCZENIE===
- zdanie
- zdanie
- zdanie

===WNIOSKI===
- punkt
- punkt
- punkt

Zasady:
- 3–5 zdań
- 3–5 punktów
- tylko informacje z tekstu
- brak XML, brak linków, brak spekulacji
"""

    tekst = (
        wczytaj_wszystkie_pdf(KATALOG_PDF)
        + "\n"
        + wczytaj_wszystkie_excel(KATALOG_XLSX)
    )

    wynik = llm_call(prompt, tekst)

    streszczenie = []
    wnioski = []

    if "===STRESZCZENIE===" in wynik and "===WNIOSKI===" in wynik:
        s, w = wynik.split("===WNIOSKI===")
        streszczenie = [
            l.lstrip("- ").strip()
            for l in s.replace("===STRESZCZENIE===", "").splitlines()
            if l.strip()
        ]
        wnioski = [
            l.lstrip("- ").strip()
            for l in w.splitlines()
            if l.strip()
        ]
    else:
        print("⚠️ LLM zwrócił niepoprawny format")

    return {
        "streszczenie": " ".join(streszczenie),
        "kluczowe_wnioski": wnioski
    }

# =====================
# REQUESTS SESSION
# =====================

def requests_session():
    session = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=1.5,
        status_forcelist=[429,500,502,503,504],
        allowed_methods=["GET"]
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session

SESSION = requests_session()

def safe_get(url, timeout=10):
    try:
        r = SESSION.get(
            url,
            timeout=timeout,
            headers={"User-Agent": "Mozilla/5.0 (NewsletterBot/1.0)"}
        )
        r.raise_for_status()
        return r.text
    except Exception:
        return None

# =====================
# SCRAPER NEWSÓW
# =====================

def is_recent(date):
    return date >= datetime.now(timezone.utc) - timedelta(days=8)

def pobierz_date(soup):
    meta = soup.find("meta", {"property":"article:published_time"})
    if meta and meta.get("content"):
        try:
            return datetime.fromisoformat(meta["content"].replace("Z","+00:00"))
        except:
            pass
    return None

def pobierz_linki_artykulow(url):
    html = safe_get(url)
    if not html:
        return []
    soup = BeautifulSoup(html,"html.parser")
    linki = set()
    for a in soup.find_all("a", href=True):
        if "/article/" in a["href"] or "/press" in a["href"]:
            linki.add(urljoin(url, a["href"]))
    return list(linki)

def pobierz_tekst(url):
    html = safe_get(url)
    if not html:
        return None
    soup = BeautifulSoup(html,"html.parser")
    data = pobierz_date(soup)
    if not data or not is_recent(data):
        return None
    return " ".join(p.get_text() for p in soup.find_all("p")[:12])

def scraper_news():
    with open(SCIEZKA_SOURCES, encoding="utf-8") as f:
        sources = json.load(f)

    prompt = """
Streść artykuł DOKŁADNIE w 2 zdaniach.
Neutralnie, faktograficznie, bez ocen.
"""

    newsy = []

    def process(src, link):
        tekst = pobierz_tekst(link)
        if not tekst:
            return None
        out = llm_call(prompt, tekst, timeout=120)
        if out:
            return {
                "tytul": src["name"],
                "podsumowanie": out,
                "link": link
            }
        return None

    with ThreadPoolExecutor(max_workers=4) as ex:
        futures = []
        for src in sources:
            print(f"🌐 {src['name']}")
            for link in pobierz_linki_artykulow(src["url"])[:4]:
                futures.append(ex.submit(process, src, link))

        for f in as_completed(futures):
            r = f.result()
            if r:
                newsy.append(r)

    return newsy

# =====================
# HTML
# =====================

def generuj_email(dane):
    env = Environment(loader=FileSystemLoader(KATALOG_SZABLONOW))
    tpl = env.get_template("email_template.html")
    html = tpl.render(**dane)
    SCIEZKA_WYNIKOWA.write_text(html, encoding="utf-8")

# =====================
# MAIN
# =====================

def main():
    print("📄 PDF + Excel")
    dokumenty = podsumowanie_dokumentow()

    print("📰 News")
    newsy = scraper_news()

    print("✉️ HTML")
    generuj_email({
        "streszczenie": dokumenty["streszczenie"],
        "kluczowe_wnioski": dokumenty["kluczowe_wnioski"],
        "najwazniejsze_news": newsy
    })

    print(f"✅ GOTOWE → {SCIEZKA_WYNIKOWA}")

if __name__ == "__main__":
    main()
