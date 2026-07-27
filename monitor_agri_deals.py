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


# Prowess Advisors logo, base64-embedded so the email is self-contained
# (no external image hosting needed, works reliably across email clients)
PROWESS_LOGO_BASE64 = (
    "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"
)

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
                <td style="background:#0f3d54;padding:0;">
                  <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
                    <tr>
                      <td style="padding:32px 36px;vertical-align:middle;">
                        <div style="font-size:22px;font-weight:800;color:#ffffff;letter-spacing:0.01em;">India Food &amp; Agri Deal Alert</div>
                        <div style="font-size:13px;color:#9fb8c7;margin-top:8px;">{today}</div>
                      </td>
                      <td style="padding:32px 36px;vertical-align:middle;text-align:right;white-space:nowrap;">
                        <img src="data:image/png;base64,{PROWESS_LOGO_BASE64}" alt="Prowess Advisors" style="height:34px;width:auto;display:inline-block;" />
                      </td>
                    </tr>
                  </table>
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
