"""
RSS FEED SCRAPER - Bardziej niezawodna alternatywa

Ten skrypt używa RSS feedów zamiast scrapowania HTML.
RSS feedy są:
- Łatwiejsze do parsowania
- Mniej podatne na blokowanie
- Szybsze
- Bardziej niezawodne

Wymaga: feedparser
pip install feedparser --break-system-packages
"""

import json
import feedparser
import requests
from datetime import datetime, timedelta, timezone
from pathlib import Path
import time

# Przykładowe RSS feedy dla rynku stali
RSS_SOURCES = {
    "Reuters Metals": "https://www.reuters.com/business/energy/rss",
    "World Steel": "https://worldsteel.org/media-centre/press-releases/rss/",
    "SteelOrbis": "https://www.steelorbis.com/rss/steel-news.xml",
    "Metal Bulletin": "https://www.metalbulletin.com/rss/steel",
    # Dodaj więcej według potrzeb
}

def get_rss_articles(feed_url, days_back=14):
    """
    Pobiera artykuły z RSS feed
    """
    try:
        print(f"   Pobieram RSS: {feed_url[:60]}...")
        feed = feedparser.parse(feed_url)
        
        if not feed.entries:
            print(f"   ⚠️ Brak wpisów w feedzie")
            return []
        
        articles = []
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=days_back)
        
        for entry in feed.entries[:20]:  # max 20 najnowszych
            # Pobierz datę
            published = None
            if hasattr(entry, 'published_parsed') and entry.published_parsed:
                try:
                    published = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
                except:
                    pass
            
            # Sprawdź czy artykuł jest świeży
            if published and published >= cutoff_date:
                articles.append({
                    'title': entry.get('title', 'No title'),
                    'link': entry.get('link', ''),
                    'summary': entry.get('summary', entry.get('description', '')),
                    'published': published,
                    'content': entry.get('content', [{}])[0].get('value', '') if entry.get('content') else ''
                })
        
        print(f"   ✓ Znaleziono {len(articles)} świeżych artykułów")
        return articles
        
    except Exception as e:
        print(f"   ❌ Błąd RSS: {e}")
        return []

def scraper_news_rss():
    """
    Zbiera newsy z RSS feedów
    """
    all_articles = []
    
    for source_name, feed_url in RSS_SOURCES.items():
        print(f"\n🌐 {source_name}")
        articles = get_rss_articles(feed_url)
        
        for article in articles:
            all_articles.append({
                'source': source_name,
                'title': article['title'],
                'link': article['link'],
                'text': article['summary'] or article['content'],
                'date': article['published']
            })
        
        time.sleep(1)  # uprzejme opóźnienie
    
    return all_articles

# =====================
# INTEGRACJA Z main.py
# =====================

def scraper_news_hybrid():
    """
    Hybrydowe podejście: próbuj RSS, fallback na scraping
    """
    print("\n1️⃣ Próba RSS feeds...")
    rss_articles = scraper_news_rss()
    
    if len(rss_articles) >= 5:
        print(f"✅ RSS wystarczy: {len(rss_articles)} artykułów")
        return rss_articles
    
    print(f"⚠️ RSS zwróciło tylko {len(rss_articles)} artykułów")
    print("2️⃣ Uzupełniam przez scraping HTML...")
    
    # Tu wywołaj oryginalny scraper jako backup
    # from main import scraper_news
    # html_articles = scraper_news()
    # return rss_articles + html_articles
    
    return rss_articles

# =====================
# STANDALONE TEST
# =====================

if __name__ == "__main__":
    print("Test RSS Scraper")
    print("=" * 60)
    
    articles = scraper_news_rss()
    
    print(f"\n📊 Zebrano {len(articles)} artykułów")
    
    for i, art in enumerate(articles[:5], 1):
        print(f"\n{i}. {art['source']}")
        print(f"   {art['title']}")
        print(f"   {art['link']}")
        print(f"   Data: {art['date']}")
        print(f"   Tekst: {art['text'][:150]}...")
