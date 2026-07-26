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
from email.header import Header
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

# Build search queries: each sector combined with a short OR-group of
# broad transaction terms. Google News RSS becomes unreliable with long
# OR chains, so we keep this list short — the precise deal type (from the
# full TRANSACTION_TYPES list above) is still extracted accurately by
# Gemini from each article's actual text, regardless of which broad term
# matched the search.
_SEARCH_TERMS = ["acquisition", "merger", "investment", "stake sale", "IPO", "partnership"]

def _format_term(term):
    return f'"{term}"' if " " in term else term

_transaction_or_group = " OR ".join(_format_term(t) for t in _SEARCH_TERMS)
SEARCH_QUERIES = [
    f"India {sector} ({_transaction_or_group}) when:{LOOKBACK_HOURS}h" for sector in SECTORS
]


GEMINI_MODEL = "gemini-flash-latest"
GEMINI_API_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"


# ---------------------------------------------------------------------
# STEP 1: Fetch candidate news via Google News RSS
# ---------------------------------------------------------------------
def fetch_news():
    articles = []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)

    # Google News (and many sites) silently reject or short-change requests
    # that don't look like they're from a real browser. feedparser's default
    # request has no such header, so we fetch manually with one.
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-IN,en;q=0.9",
    }

    total_raw_entries = 0
    failed_queries = 0
    all_published_dates = []

    for query in SEARCH_QUERIES:
        url = f"https://news.google.com/rss/search?q={requests.utils.quote(query)}&hl=en-IN&gl=IN&ceid=IN:en"
        try:
            resp = requests.get(url, headers=headers, timeout=15)
            status = resp.status_code
            feed = feedparser.parse(resp.content)
        except Exception as e:
            print(f"  [fetch error] query={query!r} exception={e}")
            failed_queries += 1
            continue

        n_entries = len(feed.entries)
        total_raw_entries += n_entries

        if status != 200 or (n_entries == 0 and feed.get("bozo")):
            print(f"  [diagnostic] query={query!r} http_status={status} entries={n_entries} bozo={feed.get('bozo')} bozo_exception={feed.get('bozo_exception')}")

        for entry in feed.entries:
            try:
                published = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
            except Exception:
                published = datetime.now(timezone.utc)

            all_published_dates.append(published)

            if published < cutoff:
                continue

            articles.append({
                "title": entry.title,
                "link": entry.link,
                "published": published.isoformat(),
                "summary": getattr(entry, "summary", ""),
            })

    print(f"Raw RSS entries across all queries (before date filtering): {total_raw_entries}. Failed queries: {failed_queries}/{len(SEARCH_QUERIES)}.")
    if all_published_dates:
        newest = max(all_published_dates)
        oldest = min(all_published_dates)
        print(f"Publish date range in raw results: oldest={oldest.isoformat()}, newest={newest.isoformat()}. Cutoff (now - {LOOKBACK_HOURS}h)={cutoff.isoformat()}.")

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
BATCH_SIZE = 15  # articles per Gemini call — cuts ~28 calls/run down to ~2


def _chunked(items, size):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def classify_batch(articles):
    """Classifies a batch of articles in a single Gemini call. Returns a list
    of dicts (same length/order as `articles` is not guaranteed — match back
    by index using the 'i' field each result carries)."""

    numbered_articles = "\n\n".join(
        f"[{i}] Title: {a['title']}\nSnippet: {a['summary']}\nPublished: {a['published']}"
        for i, a in enumerate(articles)
    )

    prompt = f"""You are filtering Indian business news for genuine Food & Agriculture
sector TRANSACTIONS only (acquisitions, mergers, investments, joint ventures,
stake sales, IPOs, asset/plant acquisitions, distribution partnerships).

Below are {len(articles)} numbered articles. For EACH one, decide if it is a
genuine Food/Agri transaction in India.

{numbered_articles}

Respond ONLY with a JSON array, no other text, no markdown fences. Include
one object per article that IS a genuine deal (skip non-deal articles
entirely — do not include them in the array). Each object must have this
exact format:
{{
  "i": <the article's number from above, as an integer>,
  "deal_date": "DD-Mon-YYYY, use the article's published/reported date if mentioned, otherwise today's date",
  "buyer": "the buyer's core company name only, no legal suffixes like Ltd/Pvt Ltd/Limited and no descriptive words",
  "target": "the target's core company/brand name only, no legal suffixes like Ltd/Pvt Ltd/Limited and no descriptive words",
  "deal_type": "Acquisition / Merger / Fund Raising / PE Investment / VC Investment / Joint Venture / Strategic Partnership / Stake Sale / IPO / Asset Acquisition / Plant Acquisition / Distribution Partnership",
  "sector": "...",
  "deal_value": "e.g. ₹245 Crore or 'Undisclosed'",
  "summary": "one or two sentence plain-English summary"
}}

If none of the {len(articles)} articles are genuine deals, respond with: []
"""

    headers = {"content-type": "application/json"}
    params = {"key": GEMINI_API_KEY}
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": 4000, "temperature": 0.2},
    }

    max_retries = 5
    for attempt in range(max_retries):
        try:
            resp = requests.post(GEMINI_API_URL, headers=headers, params=params, json=body, timeout=60)

            if resp.status_code == 429:
                wait = 20 * (attempt + 1)
                print(f"Rate limited (429) on a batch of {len(articles)} articles — waiting {wait}s (attempt {attempt+1}/{max_retries})")
                time.sleep(wait)
                continue

            resp.raise_for_status()
            data = resp.json()
            text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
            text = text.replace("```json", "").replace("```", "").strip()

            # Extract the outermost [...] array in case of stray text.
            start = text.find("[")
            end = text.rfind("]")
            if start == -1 or end == -1 or end < start:
                raise ValueError(f"No JSON array found in response: {text[:200]!r}")
            text = text[start:end + 1]

            results = json.loads(text)

            # Attach the original article's link/title back onto each result
            deals = []
            for r in results:
                idx = r.get("i")
                if idx is None or not (0 <= idx < len(articles)):
                    continue
                r["link"] = articles[idx]["link"]
                deals.append(r)

            return deals, False  # (deals found, had_error)

        except Exception as e:
            print(f"Batch classification failed ({len(articles)} articles): {e}")
            return [], True

    print(f"Giving up on a batch of {len(articles)} articles after {max_retries} rate-limit retries.")
    return [], True


# ---------------------------------------------------------------------
# STEP 3: Dedup against previously emailed deals
# ---------------------------------------------------------------------
def load_seen():
    """Returns (seen_links: set, seen_deal_keys: set)."""
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE, "r") as f:
            data = json.load(f)
        # Support the old format (a plain list of links) for backward compat.
        if isinstance(data, list):
            return set(data), set()
        return set(data.get("links", [])), set(data.get("deal_keys", []))
    return set(), set()


def save_seen(seen_links, seen_deal_keys):
    with open(SEEN_FILE, "w") as f:
        json.dump({"links": list(seen_links), "deal_keys": list(seen_deal_keys)}, f)


def deal_key(deal):
    """A normalized signature identifying the underlying deal, so the same
    transaction reported by multiple outlets is only counted/emailed once."""
    buyer = (deal.get("buyer") or "").strip().lower()
    target = (deal.get("target") or "").strip().lower()
    deal_type = (deal.get("deal_type") or "").strip().lower()
    return f"{buyer}|{target}|{deal_type}"


# ---------------------------------------------------------------------
# STEP 4: Build and send email
# ---------------------------------------------------------------------

# Badge colors per deal type, used to visually distinguish rows at a glance
_DEAL_TYPE_COLORS = {
    "acquisition": "#2563eb",
    "merger": "#7c3aed",
    "fund raising": "#059669",
    "pe investment": "#059669",
    "vc investment": "#059669",
    "joint venture": "#d97706",
    "strategic partnership": "#d97706",
    "stake sale": "#dc2626",
    "ipo": "#0891b2",
    "asset acquisition": "#2563eb",
    "plant acquisition": "#2563eb",
    "distribution partnership": "#d97706",
}


def _badge(text, color):
    return (
        f'<span style="display:inline-block;padding:3px 10px;border-radius:12px;'
        f'background:{color}1a;color:{color};font-size:12px;font-weight:600;'
        f'white-space:nowrap;">{text}</span>'
    )


def send_email(deals):
    if not deals:
        print("No new deals found — no email sent.")
        return

    today = datetime.now().strftime("%d %b %Y")
    subject = f"🌾 India Food & Agri Alert – {len(deals)} new deal(s) – {today}"

    rows_html = ""
    for i, d in enumerate(deals):
        deal_type = d.get("deal_type", "") or "—"
        color = _DEAL_TYPE_COLORS.get(deal_type.strip().lower(), "#475569")
        row_bg = "#ffffff" if i % 2 == 0 else "#f8fafc"

        rows_html += f"""
        <tr style="background:{row_bg};">
          <td style="padding:14px 16px;border-bottom:1px solid #e5e7eb;font-size:13px;color:#64748b;white-space:nowrap;">{d.get('deal_date', '')}</td>
          <td style="padding:14px 16px;border-bottom:1px solid #e5e7eb;font-size:14px;color:#0f172a;font-weight:600;">{d.get('buyer', '')}</td>
          <td style="padding:14px 16px;border-bottom:1px solid #e5e7eb;font-size:14px;color:#0f172a;">→ {d.get('target', '')}</td>
          <td style="padding:14px 16px;border-bottom:1px solid #e5e7eb;">{_badge(deal_type, color)}</td>
          <td style="padding:14px 16px;border-bottom:1px solid #e5e7eb;font-size:13px;color:#334155;">{d.get('sector', '')}</td>
          <td style="padding:14px 16px;border-bottom:1px solid #e5e7eb;font-size:13px;color:#0f172a;font-weight:600;">{d.get('deal_value', '')}</td>
          <td style="padding:14px 16px;border-bottom:1px solid #e5e7eb;">
            <a href="{d.get('link', '')}" style="color:#059669;font-size:13px;font-weight:600;text-decoration:none;">View →</a>
          </td>
        </tr>
        """

    html = f"""
    <html>
    <body style="margin:0;padding:0;background:#f1f5f9;font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f1f5f9;padding:24px 0;">
        <tr>
          <td align="center">
            <table role="presentation" width="720" cellpadding="0" cellspacing="0" style="background:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,0.08);">

              <!-- Header -->
              <tr>
                <td style="background:linear-gradient(135deg,#166534,#059669);padding:28px 32px;">
                  <div style="font-size:20px;font-weight:700;color:#ffffff;">🌾 India Food & Agri Deal Alert</div>
                  <div style="font-size:13px;color:#d1fae5;margin-top:4px;">{today} &nbsp;•&nbsp; {len(deals)} new transaction{'s' if len(deals) != 1 else ''} detected</div>
                </td>
              </tr>

              <!-- Table -->
              <tr>
                <td style="padding:8px 0;">
                  <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
                    <tr style="background:#f8fafc;">
                      <th style="padding:10px 16px;text-align:left;font-size:11px;color:#64748b;text-transform:uppercase;letter-spacing:0.05em;">Date</th>
                      <th style="padding:10px 16px;text-align:left;font-size:11px;color:#64748b;text-transform:uppercase;letter-spacing:0.05em;">Buyer</th>
                      <th style="padding:10px 16px;text-align:left;font-size:11px;color:#64748b;text-transform:uppercase;letter-spacing:0.05em;">Target</th>
                      <th style="padding:10px 16px;text-align:left;font-size:11px;color:#64748b;text-transform:uppercase;letter-spacing:0.05em;">Type</th>
                      <th style="padding:10px 16px;text-align:left;font-size:11px;color:#64748b;text-transform:uppercase;letter-spacing:0.05em;">Sector</th>
                      <th style="padding:10px 16px;text-align:left;font-size:11px;color:#64748b;text-transform:uppercase;letter-spacing:0.05em;">Value</th>
                      <th style="padding:10px 16px;text-align:left;font-size:11px;color:#64748b;text-transform:uppercase;letter-spacing:0.05em;">Source</th>
                    </tr>
                    {rows_html}
                  </table>
                </td>
              </tr>

              <!-- Footer -->
              <tr>
                <td style="padding:20px 32px;background:#f8fafc;border-top:1px solid #e5e7eb;">
                  <div style="font-size:12px;color:#94a3b8;line-height:1.6;">
                    Summaries omitted for readability — click "View" to read the full source article.<br>
                    Automated alert generated from India Food &amp; Agri sector news monitoring.
                  </div>
                </td>
              </tr>

            </table>
          </td>
        </tr>
      </table>
    </body>
    </html>
    """

    msg = MIMEMultipart("alternative")
    msg["Subject"] = Header(subject, "utf-8")
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
    seen_links, seen_deal_keys = load_seen()
    articles = fetch_news()
    print(f"Fetched {len(articles)} candidate articles.")

    # Only classify articles we haven't already processed before.
    new_articles = [a for a in articles if a["link"] not in seen_links]
    print(f"{len(new_articles)} of those are new (not previously seen).")

    candidate_deals = []
    newly_seen_links = set(seen_links)
    batches = list(_chunked(new_articles, BATCH_SIZE))
    print(f"Classifying in {len(batches)} batch(es) of up to {BATCH_SIZE} articles each.")

    for batch_num, batch in enumerate(batches, start=1):
        deals, had_error = classify_batch(batch)
        print(f"Batch {batch_num}/{len(batches)}: {len(batch)} articles -> {len(deals)} deal(s) found. Error: {had_error}")

        candidate_deals.extend(deals)

        # Only blacklist this batch's articles if the batch was actually
        # evaluated successfully. If it errored/rate-limited, leave them
        # unmarked so they get retried next run instead of being lost.
        if not had_error:
            for article in batch:
                newly_seen_links.add(article["link"])

        if batch_num < len(batches):
            time.sleep(20)  # pause between batches to respect rate limits

    # Dedupe: multiple articles from different outlets often cover the same
    # underlying deal. Keep only the first occurrence of each (buyer, target,
    # deal_type) signature, and skip any deal already emailed in a past run.
    new_deals = []
    newly_seen_deal_keys = set(seen_deal_keys)
    for deal in candidate_deals:
        key = deal_key(deal)
        if key in newly_seen_deal_keys:
            continue
        newly_seen_deal_keys.add(key)
        new_deals.append(deal)

    send_email(new_deals)
    save_seen(newly_seen_links, newly_seen_deal_keys)


if __name__ == "__main__":
    main()
