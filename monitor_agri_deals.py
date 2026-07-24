"""
India Food & Agri Business Transaction Alert Script
----------------------------------------------------
Fetches recent news, uses Gemini to filter for genuine Food & Agri
transactions (M&A, investment, JV, stake sale, etc.) happening in India,
deduplicates against previously sent items, and emails a summary table.

Run on a schedule (e.g. via GitHub Actions cron) — see
.github/workflows/agri-alert.yml
"""

import os
import json
import time
import smtplib
import feedparser
import requests
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta, timezone

# ---------------------------------------------------------------------
# CONFIG — edit these or set as environment variables / GitHub Secrets
# ---------------------------------------------------------------------
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "YOUR_GEMINI_API_KEY")
GMAIL_ADDRESS = os.environ.get("GMAIL_ADDRESS", "your_sender_email@gmail.com")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "your_16_char_app_password")
RECIPIENT_EMAIL = os.environ.get("RECIPIENT_EMAIL", "your_recipient_email@gmail.com")

SEEN_FILE = "seen_deals.json"
LOOKBACK_HOURS = 6  # how far back to consider "new" news each run

# Transaction types to track
TRANSACTION_TYPES = [
    "acquisition",
    "merger",
    "fund raising",
    "private equity investment",
    "venture capital investment",
    "joint venture",
    "strategic partnership",
    "stake sale",
    "IPO",
    "asset acquisition",
    "plant acquisition",
    "distribution partnership",
]

# Food & Agri sectors to track
SECTORS = [
    "Rice",
    "Wheat",
    "Pulses",
    "Edible Oils",
    "Palm Oil",
    "Soybean Oil",
    "Sunflower Oil",
    "Mustard Oil",
    "Tea",
    "Coffee",
    "Spices",
    "Cashew",
    "Dry Fruits",
    "Fruits & Vegetables",
    "Marine Products",
    "Poultry",
    "Dairy",
    "Meat Processing",
    "Frozen Foods",
    "Food Ingredients",
    "Bakery",
    "Beverages",
    "Animal Feed",
    "Fertilizers",
    "Seeds",
    "Agrochemicals",
    "Farm Machinery",
    "AgriTech",
]

# Build search queries: each sector combined with an OR-group of all
# transaction types, e.g. "India Rice (acquisition OR merger OR ... )"
# This keeps the number of RSS calls per run equal to len(SECTORS)
# instead of len(SECTORS) x len(TRANSACTION_TYPES).
_transaction_or_group = " OR ".join(TRANSACTION_TYPES)
SEARCH_QUERIES = [
    f"India {sector} ({_transaction_or_group})" for sector in SECTORS
]


GEMINI_MODEL = "gemini-2.5-flash"
GEMINI_API_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"


# ---------------------------------------------------------------------
# STEP 1: Fetch candidate news via Google News RSS
# ---------------------------------------------------------------------
def fetch_news():
    articles = []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)

    for query in SEARCH_QUERIES:
        url = f"https://news.google.com/rss/search?q={requests.utils.quote(query)}&hl=en-IN&gl=IN&ceid=IN:en"
        feed = feedparser.parse(url)

        for entry in feed.entries:
            try:
                published = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
            except Exception:
                published = datetime.now(timezone.utc)

            if published < cutoff:
                continue

            articles.append({
                "title": entry.title,
                "link": entry.link,
                "published": published.isoformat(),
                "summary": getattr(entry, "summary", ""),
            })

    # Dedupe by link within this run
    seen_links = set()
    unique_articles = []
    for a in articles:
        if a["link"] not in seen_links:
            seen_links.add(a["link"])
            unique_articles.append(a)

    return unique_articles


# ---------------------------------------------------------------------
# STEP 2: Use Gemini to classify + extract structured deal info
# ---------------------------------------------------------------------
def classify_article(article):
    prompt = f"""You are filtering Indian business news for genuine Food & Agriculture
sector TRANSACTIONS only (acquisitions, mergers, investments, joint ventures,
stake sales, IPOs, asset/plant acquisitions, distribution partnerships).

Article title: {article['title']}
Article snippet: {article['summary']}
Article published date: {article['published']}
Link: {article['link']}

If this is NOT a genuine Food/Agri transaction in India, respond with exactly:
{{"is_deal": false}}

If it IS a genuine Food/Agri transaction in India, respond ONLY with JSON in
this exact format, no other text, no markdown fences:
{{
  "is_deal": true,
  "deal_date": "DD-Mon-YYYY, use the article's published/reported date if mentioned, otherwise today's date",
  "buyer": "...",
  "target": "...",
  "deal_type": "Acquisition / Merger / Fund Raising / PE Investment / VC Investment / Joint Venture / Strategic Partnership / Stake Sale / IPO / Asset Acquisition / Plant Acquisition / Distribution Partnership",
  "sector": "...",
  "deal_value": "e.g. ₹245 Crore or 'Undisclosed'",
  "summary": "one or two sentence plain-English summary"
}}
"""

    headers = {"content-type": "application/json"}
    params = {"key": GEMINI_API_KEY}
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": 400, "temperature": 0.2},
    }

    try:
        resp = requests.post(GEMINI_API_URL, headers=headers, params=params, json=body, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
        text = text.replace("```json", "").replace("```", "").strip()
        parsed = json.loads(text)
        return parsed
    except Exception as e:
        print(f"Classification failed for '{article['title']}': {e}")
        return {"is_deal": False}


# ---------------------------------------------------------------------
# STEP 3: Dedup against previously emailed deals
# ---------------------------------------------------------------------
def load_seen():
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE, "r") as f:
            return set(json.load(f))
    return set()


def save_seen(seen_links):
    with open(SEEN_FILE, "w") as f:
        json.dump(list(seen_links), f)


# ---------------------------------------------------------------------
# STEP 4: Build and send email
# ---------------------------------------------------------------------
def send_email(deals):
    if not deals:
        print("No new deals found — no email sent.")
        return

    today = datetime.now().strftime("%d %b %Y")
    subject = f"India Food & Agri Transaction Alert – {len(deals)} new deal(s) – {today}"

    rows_html = ""
    for d in deals:
        rows_html += f"""
        <tr>
          <td>{d.get('deal_date', '')}</td>
          <td>{d.get('buyer', '')}</td>
          <td>{d.get('target', '')}</td>
          <td>{d.get('deal_type', '')}</td>
          <td>{d.get('sector', '')}</td>
          <td>{d.get('deal_value', '')}</td>
          <td><a href="{d.get('link', '')}">Link</a></td>
        </tr>
        """

    html = f"""
    <html><body>
    <h2>India Food & Agri Business Transaction Alert</h2>
    <p>{len(deals)} new transaction(s) detected:</p>
    <table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse;font-family:Arial,sans-serif;font-size:13px;">
      <tr style="background:#f2f2f2;">
        <th>Date</th><th>Buyer</th><th>Target</th><th>Deal Type</th>
        <th>Sector</th><th>Deal Value</th><th>Source</th>
      </tr>
      {rows_html}
    </table>
    <p style="font-size:12px;color:#666;">Summaries omitted from table for readability — click Source for full article.</p>
    </body></html>
    """

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = GMAIL_ADDRESS
    msg["To"] = RECIPIENT_EMAIL
    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        server.sendmail(GMAIL_ADDRESS, RECIPIENT_EMAIL, msg.as_string())

    print(f"Email sent with {len(deals)} deal(s).")


# ---------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------
def main():
    seen = load_seen()
    articles = fetch_news()
    print(f"Fetched {len(articles)} candidate articles.")

    new_deals = []
    newly_seen = set(seen)

    for article in articles:
        if article["link"] in seen:
            continue

        result = classify_article(article)
        time.sleep(1)  # gentle rate limiting

        if result.get("is_deal"):
            result["link"] = article["link"]
            new_deals.append(result)

        newly_seen.add(article["link"])

    send_email(new_deals)
    save_seen(newly_seen)


if __name__ == "__main__":
    main()
