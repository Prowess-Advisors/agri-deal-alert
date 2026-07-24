# India Food & Agri Deal Alert

Automatically emails you whenever a new Food & Agriculture business
transaction (acquisition, investment, merger, JV, stake sale, etc.) is
reported in India.

## How it works
1. Searches Google News RSS every 2 hours for Food/Agri + transaction keywords.
2. Sends each candidate article to Claude, which decides if it's a genuine
   deal and extracts buyer, target, sector, deal value, and a summary.
3. Skips deals already emailed before (tracked in `seen_deals.json`).
4. Emails you an HTML table of the new deals.

## One-time setup (about 10 minutes)

### 1. Create a GitHub repository
- Go to https://github.com/new, create a new **private** repo (e.g. `agri-deal-alert`).
- Upload these files to it: `monitor_agri_deals.py`, `requirements.txt`,
  `.github/workflows/agri-alert.yml`, this `README.md`.

### 2. Get a Gemini API key
- Go to https://aistudio.google.com/apikey and click **Create API key**.
- Copy the key.
- Gemini has a generous free tier, which should easily cover this use case
  (a handful of articles classified every couple hours).

### 3. Create a Gmail App Password (so the script can send from your Gmail)
- Your Google Account must have 2-Step Verification turned on:
  https://myaccount.google.com/security
- Then go to https://myaccount.google.com/apppasswords
- Create an app password (name it e.g. "Agri Deal Alert"), copy the 16-character code.
- **Do not use your real Gmail password anywhere in this project — only this App Password.**

### 4. Add secrets to your GitHub repo
In your repo: **Settings → Secrets and variables → Actions → New repository secret**.
Add these four:

| Secret name           | Value                                      |
|-----------------------|---------------------------------------------|
| `GEMINI_API_KEY`      | Your Gemini API key from step 2             |
| `GMAIL_ADDRESS`       | The Gmail address to send FROM              |
| `GMAIL_APP_PASSWORD`  | The 16-character app password from step 3   |
| `RECIPIENT_EMAIL`     | The email address to send alerts TO         |

### 5. Turn it on
- Go to the **Actions** tab in your repo → you should see "India Food & Agri Deal Alert".
- Click **Run workflow** to test it manually first.
- After that, it runs automatically every 2 hours (edit the `cron` line in
  `.github/workflows/agri-alert.yml` to change frequency — e.g. `0 * * * *`
  for hourly).

## Customizing
- **Sectors**: edit the `SECTORS` list in `monitor_agri_deals.py` (currently
  28 sectors: Rice, Wheat, Pulses, Edible Oils, Palm/Soybean/Sunflower/Mustard
  Oil, Tea, Coffee, Spices, Cashew, Dry Fruits, Fruits & Vegetables, Marine
  Products, Poultry, Dairy, Meat Processing, Frozen Foods, Food Ingredients,
  Bakery, Beverages, Animal Feed, Fertilizers, Seeds, Agrochemicals, Farm
  Machinery, AgriTech).
- **Transaction types**: edit the `TRANSACTION_TYPES` list (currently:
  Acquisitions, Mergers, Fund Raising, PE/VC Investments, Joint Ventures,
  Strategic Partnerships, Stake Sales, IPOs, Asset Acquisitions, Plant
  Acquisitions, Distribution Partnerships).
- Each sector is searched combined with all transaction types in one query
  (e.g. `India Rice (acquisition OR merger OR ...)`), so the script makes
  28 RSS calls per run — one per sector — rather than 28 × 12 separate calls.
- **Frequency**: edit the `cron` schedule in the workflow file.
- **Lookback window**: edit `LOOKBACK_HOURS` in the script.

## Notes & limitations
- Google News RSS is free but occasionally misses smaller regional outlets —
  you can add more specific RSS feeds (e.g. BSE/NSE announcement feeds,
  FoodDialogues, Agriculture Post) to `SEARCH_QUERIES`/fetch logic later.
- Deal values reported are only as accurate as the source news article.
- This is for information/monitoring purposes — always verify significant
  deals against the primary source before acting on them.
