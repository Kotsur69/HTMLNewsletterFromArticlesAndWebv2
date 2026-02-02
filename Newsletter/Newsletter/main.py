import json
import pdfplumber
import requests
import pandas as pd
from bs4 import BeautifulSoup
from jinja2 import Environment, FileSystemLoader
from pathlib import Path
import warnings
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlparse
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import time
import random
import re

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
MODEL_NAME = "microsoft/phi-4-mini-reasoning"

# =====================
# OPCJE KONFIGURACYJNE
# =====================

# Strategia: generuj po angielsku, potem tłumacz na polski (lepsze wyniki)
USE_ENGLISH_INTERNALLY = True  # LLM pracuje po angielsku
TRANSLATE_TO_POLISH = True     # Automatycznie tłumaczy wynik

# Minimalnie akceptowalna długość odpowiedzi LLM
MIN_SUMMARY_LENGTH = 40

# Czy używać fallback'ów gdy LLM nie działa
USE_FALLBACKS = True

# =====================
# PROSTY TŁUMACZ (bez dodatkowych bibliotek)
# =====================

# Słownik do podstawowego tłumaczenia kluczowych fraz
TRANSLATIONS = {
    # Months
    "January": "stycznia", "February": "lutego", "March": "marca",
    "April": "kwietnia", "May": "maja", "June": "czerwca",
    "July": "lipca", "August": "sierpnia", "September": "września",
    "October": "października", "November": "listopada", "December": "grudnia",
    
    # Common steel industry terms
    "steel production": "produkcja stali",
    "steel prices": "ceny stali",
    "increased": "wzrosła",
    "decreased": "spadła",
    "growth": "wzrost",
    "decline": "spadek",
    "demand": "popyt",
    "supply": "podaż",
    "capacity": "zdolności produkcyjne",
    "automotive": "motoryzacja",
    "construction": "budownictwo",
    "exports": "eksport",
    "imports": "import",
    "China": "Chiny",
    "Europe": "Europa",
    "Poland": "Polska",
    "Germany": "Niemcy",
    "market": "rynek",
    "industry": "przemysł",
    "production": "produkcja",
    "consumption": "konsumpcja",
    "prices": "ceny",
    "rose": "wzrosły",
    "fell": "spadły",
    "higher": "wyższe",
    "lower": "niższe",
    "quarter": "kwartał",
    "year": "rok",
    "month": "miesiąc",
    "percent": "procent",
    "tonnes": "ton",
    "million": "milion",
    "increased by": "wzrosła o",
    "decreased by": "spadła o",
    "compared to": "w porównaniu do",
    "due to": "z powodu",
}

def simple_translate(text):
    """
    Prosta funkcja tłumacząca kluczowe frazy z angielskiego na polski
    """
    if not text or not TRANSLATE_TO_POLISH:
        return text
    
    result = text
    
    # Sortuj od najdłuższych do najkrótszych aby uniknąć konfliktów
    for eng, pol in sorted(TRANSLATIONS.items(), key=lambda x: len(x[0]), reverse=True):
        # Case-insensitive replacement
        pattern = re.compile(re.escape(eng), re.IGNORECASE)
        result = pattern.sub(pol, result)
    
    return result

def translate_with_llm(text):
    """
    Tłumaczenie przez LLM (jeśli dostępne i działa)
    """
    if not text or len(text) < 10:
        return text
    
    system_prompt = """You are a professional translator. Translate the following text from English to Polish.

Rules:
- Maintain professional business tone
- Keep numbers and dates exactly as they are
- Preserve technical terms related to steel industry
- Output ONLY the Polish translation, nothing else"""

    try:
        translated = llm_call(system_prompt, text, timeout=60, max_retries=1)
        
        # Walidacja: czy tłumaczenie ma sens?
        if translated and len(translated) >= len(text) * 0.5:  # Przynajmniej 50% długości oryginału
            return translated
    except:
        pass
    
    # Fallback: prosty słownik
    return simple_translate(text)

# =====================
# LLM Z WALIDACJĄ
# =====================

def llm_call(system_prompt, user_text, timeout=120, max_retries=2):
    """
    Wywołanie LM Studio z walidacją i retry
    """
    for attempt in range(max_retries):
        payload = {
            "model": MODEL_NAME,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_text[:3500]}  # Zmniejszony limit dla słabego sprzętu
            ],
            "temperature": 0.3,  # Trochę wyższa dla naturalności
            "max_tokens": 180,
            "top_p": 0.9
        }

        try:
            r = requests.post(
                f"{LM_STUDIO_URL}/v1/chat/completions",
                json=payload,
                timeout=timeout
            )
            r.raise_for_status()
            data = r.json()
            
            choices = data.get("choices", [])
            if choices:
                content = choices[0].get("message", {}).get("content", "").strip()
                
                # Walidacja
                if len(content) >= MIN_SUMMARY_LENGTH:
                    # Sprawdź jakość (nie za dużo dziwnych znaków)
                    special_char_ratio = sum(1 for c in content if not c.isalnum() and c not in ' .,!?-:;()[]"\'%') / max(len(content), 1)
                    if special_char_ratio < 0.2:
                        return content
                    else:
                        print(f"   ⚠️ Próba {attempt+1}: Zbyt dużo dziwnych znaków ({special_char_ratio:.1%})")
                else:
                    print(f"   ⚠️ Próba {attempt+1}: Odpowiedź za krótka ({len(content)} znaków)")
            
            if attempt < max_retries - 1:
                print(f"   🔄 Ponawiam wywołanie LLM...")
                time.sleep(2)
                
        except requests.exceptions.Timeout:
            print(f"⏱️ Timeout LLM (próba {attempt+1})")
        except Exception as e:
            print(f"❌ Błąd LLM (próba {attempt+1}): {type(e).__name__}")
    
    return ""

# =====================
# PDF
# =====================

def wyciagnij_tekst_pdf(sciezka):
    tekst = ""
    try:
        with pdfplumber.open(sciezka) as pdf:
            for page in pdf.pages[:10]:  # Max 10 stron (dla słabego sprzętu)
                extracted = page.extract_text()
                if extracted:
                    tekst += extracted + "\n"
    except Exception as e:
        print(f"❌ Błąd PDF {sciezka.name}: {e}")
    return tekst

def wczytaj_wszystkie_pdf(katalog, limit=40_000):  # Zmniejszony limit
    calosc = ""
    if not katalog.exists():
        print(f"⚠️ Katalog PDF nie istnieje: {katalog}")
        return calosc
        
    pdfs = list(katalog.glob("*.pdf"))
    if not pdfs:
        print(f"⚠️ Brak plików PDF w {katalog}")
        return calosc
        
    for pdf in pdfs[:2]:  # Max 2 PDFy
        print(f"   → PDF: {pdf.name}")
        tekst = wyciagnij_tekst_pdf(pdf)
        calosc += tekst[:limit] + "\n\n"
    return calosc

# =====================
# EXCEL
# =====================

def streszczenie_excel(df):
    """Zwięzłe streszczenie kolumn numerycznych"""
    opis = []
    
    # Pierwsze 3 wiersze jako przykład
    for i in range(min(3, len(df))):
        row_data = []
        for col in df.columns[:4]:  # Max 4 kolumny
            val = df.iloc[i][col]
            if pd.notna(val):
                row_data.append(f"{col}: {val}")
        if row_data:
            opis.append(" | ".join(row_data))
    
    return "\n".join(opis)

def wczytaj_wszystkie_excel(katalog):
    calosc = ""
    if not katalog.exists():
        print(f"⚠️ Katalog Excel nie istnieje: {katalog}")
        return calosc
        
    xlsxs = list(katalog.glob("*.xlsx"))
    if not xlsxs:
        print(f"⚠️ Brak plików Excel w {katalog}")
        return calosc
        
    for plik in xlsxs[:2]:  # Max 2 Excele
        print(f"   → Excel: {plik.name}")
        try:
            df = pd.read_excel(plik)
            calosc += f"\n=== {plik.name} ===\n"
            calosc += streszczenie_excel(df.head(15)) + "\n"  # Tylko 15 wierszy
        except Exception as e:
            print(f"❌ Excel error {plik.name}: {e}")
    return calosc

# =====================
# FALLBACK SUMMARIES (PO POLSKU)
# =====================

def create_fallback_summary(pdf_text, excel_text):
    """
    Tworzy podstawowe podsumowanie bez LLM (fallback) - PO POLSKU
    """
    summary = "Dokumenty zawierają dane dotyczące rynku stali i produkcji."
    insights = []
    
    combined = (pdf_text + excel_text).lower()
    
    # Szukaj liczb produkcji
    numbers = re.findall(r'\d+[.,]?\d*\s*(?:ton|tonnes|mt|thousand|million|tys|mln)', combined)
    if numbers:
        insights.append(f"Wykryto dane produkcyjne: {', '.join(numbers[:3])}")
    
    # Szukaj krajów/regionów
    countries_en = re.findall(r'\b(Poland|Germany|China|USA|Europe|Asia|EU|France|Italy|Spain)\b', pdf_text)
    countries_pl = re.findall(r'\b(Polska|Niemcy|Chiny|Europa|Azja|Francja|Włochy|Hiszpania)\b', pdf_text)
    countries = countries_en + countries_pl
    if countries:
        unique = list(set(countries[:5]))
        insights.append(f"Regiony wymienione: {', '.join(unique)}")
    
    # Szukaj zmian procentowych
    percentages = re.findall(r'(\d+[.,]?\d*)\s*%', combined)
    if percentages and len(percentages) >= 2:
        insights.append(f"Zmiany: wykryto {len(percentages)} wartości procentowych")
    
    # Szukaj dat
    dates = re.findall(r'\b(2024|2025|2026)\b', combined)
    if dates:
        insights.append(f"Dane dotyczą okresu: {min(dates)}-{max(dates)}")
    
    if not insights:
        insights = [
            "Dokument zawiera dane rynkowe dotyczące przemysłu stalowego",
            "Zalecane ręczne sprawdzenie dokumentów dla szczegółów",
            "Brak wystarczających danych do automatycznej analizy"
        ]
    
    return {
        "streszczenie": summary,
        "kluczowe_wnioski": insights
    }

# =====================
# PODSUMOWANIE PDF + EXCEL
# =====================

def podsumowanie_dokumentow():
    """
    Tworzy podsumowanie z PDF i Excel - PO POLSKU
    """
    # Zawsze używaj angielskiego dla LLM (lepsze wyniki)
    system_prompt = """You are a steel market analyst creating an executive newsletter.

Respond ONLY in this exact format:

SUMMARY:
[2-3 clear sentences about the steel market situation - be specific with numbers and facts]

INSIGHTS:
- [specific insight with data]
- [specific insight with data]
- [specific insight with data]

Rules:
- Write in ENGLISH (will be translated to Polish)
- Be concrete - include numbers, percentages, dates
- Focus on: prices, demand, production, trends
- No speculation, only facts from the documents
- Keep it concise and professional"""

    pdf_text = wczytaj_wszystkie_pdf(KATALOG_PDF)
    excel_text = wczytaj_wszystkie_excel(KATALOG_XLSX)
    tekst = pdf_text + "\n" + excel_text

    if not tekst.strip():
        print("⚠️ Brak danych z PDF/Excel")
        return {
            "streszczenie": "Brak dokumentów do analizy.",
            "kluczowe_wnioski": ["Nie znaleziono plików PDF lub Excel w katalogu"]
        }

    print("   🤖 Wywołuję LLM dla podsumowania dokumentów...")
    wynik = llm_call(system_prompt, tekst[:2500], timeout=300)  # Zmniejszony limit dla słabego sprzętu

    streszczenie = ""
    wnioski = []

    if wynik and len(wynik) >= MIN_SUMMARY_LENGTH:
        try:
            # Parsowanie angielskiej odpowiedzi
            if "SUMMARY:" in wynik and "INSIGHTS:" in wynik:
                parts = wynik.split("INSIGHTS:")
                streszczenie_en = parts[0].replace("SUMMARY:", "").strip()
                wnioski_raw = parts[1].strip()
                
                wnioski_en = [
                    line.lstrip("- •*123456789.").strip()
                    for line in wnioski_raw.splitlines()
                    if line.strip() and len(line.strip()) > 15
                ]
                
                # Tłumaczenie na polski
                print("   🔄 Tłumaczę na polski...")
                streszczenie = translate_with_llm(streszczenie_en)
                wnioski = [translate_with_llm(w) for w in wnioski_en if w]
                
                print(f"   ✓ Przetłumaczono: streszczenie + {len(wnioski)} wniosków")
                
            else:
                print("⚠️ LLM nie zwrócił poprawnego formatu")
                raise ValueError("Bad format")
                
        except Exception as e:
            print(f"⚠️ Błąd parsowania: {e}")
            if USE_FALLBACKS:
                print("   ↪️ Używam fallback (po polsku)")
                return create_fallback_summary(pdf_text, excel_text)
    else:
        print("⚠️ LLM zwrócił niepoprawną odpowiedź")
        if USE_FALLBACKS:
            print("   ↪️ Używam fallback (po polsku)")
            return create_fallback_summary(pdf_text, excel_text)

    # Walidacja
    if len(wnioski) == 0 or all(len(w) < 15 for w in wnioski):
        print("⚠️ Wnioski są zbyt krótkie")
        if USE_FALLBACKS:
            print("   ↪️ Używam fallback (po polsku)")
            return create_fallback_summary(pdf_text, excel_text)

    return {
        "streszczenie": streszczenie,
        "kluczowe_wnioski": wnioski
    }

# =====================
# RSS FEEDS
# =====================

def get_rss_news():
    """
    Pobiera newsy z RSS
    """
    try:
        import feedparser
    except ImportError:
        print("⚠️ Brak feedparser - zainstaluj: pip install feedparser --break-system-packages")
        return []
    
    rss_feeds = {
        "SteelOrbis": "https://www.steelorbis.com/steel-news/rss.xml",
        "WorldSteel": "https://worldsteel.org/media-centre/press-releases/rss/",
    }
    
    all_news = []
    cutoff = datetime.now(timezone.utc) - timedelta(days=14)
    
    for source, feed_url in rss_feeds.items():
        try:
            print(f"   📡 RSS: {source}")
            feed = feedparser.parse(feed_url)
            
            for entry in feed.entries[:5]:  # Max 5 z każdego źródła
                published = None
                if hasattr(entry, 'published_parsed') and entry.published_parsed:
                    try:
                        published = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
                    except:
                        pass
                
                if published and published >= cutoff:
                    text = entry.get('summary', entry.get('description', ''))
                    if len(text) > 80:
                        all_news.append({
                            'source': source,
                            'title': entry.get('title', 'No title'),
                            'link': entry.get('link', ''),
                            'text': text[:1000],  # Max 1000 znaków dla słabego sprzętu
                            'date': published
                        })
            
            time.sleep(1)
        except Exception as e:
            print(f"   ❌ RSS błąd {source}: {type(e).__name__}")
    
    return all_news

# =====================
# SCRAPER NEWSÓW
# =====================

def scraper_news():
    """
    Główna funkcja pobierania newsów - PO POLSKU
    """
    print("\n📰 Pobieram newsy...")
    
    print("\n1️⃣ Pobieram z RSS feeds...")
    rss_articles = get_rss_news()
    
    if len(rss_articles) >= 3:
        print(f"   ✅ Znaleziono {len(rss_articles)} artykułów")
    else:
        print(f"   ⚠️ Tylko {len(rss_articles)} artykułów")
    
    # Przetwórz artykuły
    newsy = []
    
    system_prompt = """Summarize this steel industry news article in 2-3 sentences.

Rules:
- Write in ENGLISH (will be translated)
- Be specific - include numbers, dates, companies
- Focus on market impact
- Professional tone
- Just the summary, nothing else"""
    
    max_articles = min(len(rss_articles), 6)  # Max 6 dla słabego sprzętu
    
    for idx, article in enumerate(rss_articles[:max_articles], 1):
        print(f"   [{idx}/{max_articles}] {article['title'][:50]}...")
        
        summary_en = llm_call(system_prompt, article['text'], timeout=90, max_retries=1)
        
        if summary_en and len(summary_en) >= 40:
            # Ogranicz do 3 zdań
            sentences = re.split(r'[.!?]+', summary_en)
            sentences = [s.strip() for s in sentences if len(s.strip()) > 10]
            if len(sentences) > 3:
                summary_en = ". ".join(sentences[:3]) + "."
            
            # Tłumacz na polski
            summary_pl = translate_with_llm(summary_en)
            
            newsy.append({
                "tytul": article['source'],
                "podsumowanie": summary_pl,
                "link": article['link']
            })
            print(f"      ✅ Dodano (przetłumaczono)")
        else:
            # Fallback - użyj pierwsze 2 zdania z oryginału + prosty słownik
            text = article['text']
            sentences = re.split(r'[.!?]+', text)
            sentences = [s.strip() for s in sentences if len(s.strip()) > 20]
            if len(sentences) >= 2:
                fallback = ". ".join(sentences[:2]) + "."
                fallback_pl = simple_translate(fallback)
                
                newsy.append({
                    "tytul": article['source'],
                    "podsumowanie": fallback_pl,
                    "link": article['link']
                })
                print(f"      ✅ Dodano (fallback)")
        
        time.sleep(1)  # Łagodniej dla słabego CPU
    
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
    print("=" * 60)
    print("GENERATOR NEWSLETTERA - START")
    print("=" * 60)
    print(f"Konfiguracja:")
    print(f"  - LLM pracuje po: angielsku (lepsze wyniki)")
    print(f"  - Output: polski (automatyczne tłumaczenie)")
    print(f"  - Fallbacki: {'TAK' if USE_FALLBACKS else 'NIE'}")
    print(f"  - Model: {MODEL_NAME}")
    print("=" * 60)
    
    print("\n📄 Przetwarzam PDF + Excel...")
    dokumenty = podsumowanie_dokumentow()
    print(f"   ✓ Streszczenie: {len(dokumenty['streszczenie'])} znaków")
    print(f"   ✓ Wnioski: {len(dokumenty['kluczowe_wnioski'])} pozycji")

    newsy = scraper_news()
    print(f"\n   ✅ Zebrano: {len(newsy)} newsów")

    print("\n✉️ Generuję HTML...")
    generuj_email({
        "streszczenie": dokumenty["streszczenie"],
        "kluczowe_wnioski": dokumenty["kluczowe_wnioski"],
        "najwazniejsze_news": newsy
    })

    print("\n" + "=" * 60)
    print(f"✅ GOTOWE → {SCIEZKA_WYNIKOWA}")
    print(f"📊 Statystyki:")
    print(f"   - Streszczenie: {len(dokumenty['streszczenie'])} znaków")
    print(f"   - Wnioski: {len(dokumenty['kluczowe_wnioski'])}")
    print(f"   - Newsy: {len(newsy)}")
    print("=" * 60)

if __name__ == "__main__":
    main()