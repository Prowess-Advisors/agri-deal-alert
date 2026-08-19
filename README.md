# India Food & Agri Deal Alert

Automatically emails you whenever a new Food & Agriculture business
transaction (acquisition, investment, merger, JV, stake sale, etc.) is
reported in India.

## How it works
1. Searches Google News RSS every 3 hours across 29 Food/Agri sectors,
   using Google's `when:` operator so results are filtered for recency
   server-side (not just relevance).
2. Sends new articles to Gemini in batches (not one call per article) so
   it stays well within free-tier rate limits. Gemini decides which are
   genuine deals and extracts buyer, target, sector, deal value, and a
   summary.
3. Dedupes by both article link and by the underlying deal itself (buyer +
   target + deal type), so the same transaction covered by multiple outlets
   is only ever emailed once.
4. Emails you a styled HTML table of the new deals, with your logo in the
   header.

## One-time setup (about 10 minutes)

### 1. Create a GitHub repository
- Go to https://github.com/new, create a repo (e.g. `agri-deal-alert`).
- Upload these files, preserving the folder structure:
  - `monitor_agri_deals.py`
  - `requirements.txt`
  - `logo.png` (your Prowess Advisors logo — must sit in the repo root,
    next to `monitor_agri_deals.py`)
  - `.github/workflows/agri-alert.yml`
  - this `README.md`

### 2. Get a Gemini API key
- Go to https://aistudio.google.com/apikey and click **Create API key**.
- Copy the key.

### 3. Create a Gmail App Password
- Turn on 2-Step Verification: https://myaccount.google.com/security
- Then go to https://myaccount.google.com/apppasswords
- Create an app password, copy the 16-character code.
- Never use your real Gmail password anywhere in this project — only this
  App Password.

### 4. Add secrets to your GitHub repo
**Settings → Secrets and variables → Actions → New repository secret**:

| Secret name           | Value                                      |
|-----------------------|---------------------------------------------|
| `GEMINI_API_KEY`      | Your Gemini API key from step 2             |
| `GMAIL_ADDRESS`       | The Gmail address to send FROM              |
| `GMAIL_APP_PASSWORD`  | The 16-character app password from step 3   |
| `RECIPIENT_EMAIL`     | The email address to send alerts TO         |

### 5. Turn it on
- Go to the **Actions** tab → "India Food & Agri Deal Alert" → **Run workflow**
  to test it manually first.
- After that it runs automatically every 3 hours.

## Customizing
- **Sectors**: edit the `SECTORS` list in `monitor_agri_deals.py`.
- **Frequency**: edit the `cron` schedule in the workflow file
  (`0 */3 * * *` = every 3 hours; `0 * * * *` = hourly).
- **Lookback window**: edit `LOOKBACK_HOURS` in the script (keep it >= the
  gap between scheduled runs, so nothing falls in a gap).
- **Batch size / pacing**: `BATCH_SIZE` and the `time.sleep(20)` between
  batches control how many Gemini calls are made per run and how fast —
  tune these if you hit rate limits.

## Notes & limitations
- Private repos get a limited free tier of GitHub Actions minutes/month;
  if you hit billing errors, either make the repo public (unlimited free
  minutes) or reduce the schedule frequency.
- Google News RSS is free but occasionally misses smaller regional outlets.
- Deal values are only as accurate as the source article.
- This is for information/monitoring purposes — verify significant deals
  against the primary source before acting on them.
