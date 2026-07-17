# Snowhouse Health Check Skill

A Cortex Code skill that generates deep-dive Snowflake cost and efficiency health check PDFs from Snowhouse internal metering data. Built for AEs and SEs preparing customer health reviews, QBRs, and EBRs — **no SE required to run it**.

---

## What You Get

A **14-page deep-dive PDF** built from actual hourly metering data, with no estimated savings figures:

| Page | Section |
|------|---------|
| 1 | Cover — company, account, region, credit rate, analysis period |
| 2 | Executive Summary — KPI tiles, 12-month stacked bar, monthly credit table |
| 3 | Warehouse Credit Breakdown — 90-day bar chart + per-warehouse stats table |
| 4 | Hourly Activity Patterns — heatmap showing when each warehouse runs (UTC) |
| 5 | Weekend & Overnight Activity Audit — identifies idle/over-scheduled warehouses |
| 6 | Sizing & Multi-Cluster Candidacy — box plot + sizing table with CV and peak/median |
| 7–8 | Per-Warehouse Recommendations — specific sizing, multi-cluster, auto-suspend guidance |
| 9–11 | 6 SQL Scripts — ready to run in the customer's account for query-level investigation |
| 12 | Priority Action Matrix — top-5 warehouses with specific numbered actions |
| 13 | AI & Cortex Adoption (if AI usage exists) |
| 14 | Infrastructure Overview + Appendix |

---

## Prerequisites

You need these three things before running the skill for the first time:

### 1. Cortex Code Desktop
Download from go/coco (internal Snowflake link).

### 2. uv (Python package runner)
```bash
brew install uv
```
Or see https://docs.astral.sh/uv/getting-started/installation/

### 3. SNOWHOUSE_AWS_US_WEST_2 connection in CoCo
In CoCo's connection settings, add a Snowflake connection named exactly:
```
SNOWHOUSE_AWS_US_WEST_2
```
pointing to the Snowhouse account. This uses your Okta SSO — the first run will pop up a browser login. After that it caches the session.

> If you don't know the Snowhouse account details, ask your SE or check #snowhouse-users in Slack.

---

## Install the Skill

In your terminal:
```bash
git clone https://github.com/sfc-gh-kweinerman/snowhouse-health-check.git \
  ~/.snowflake/cortex/skills/snowhouse-health-check
```

Then restart Cortex Code Desktop (Cmd+Shift+P → "Reload Window" or just quit and reopen).

Or in CoCo, use the `github-plugin-installer` skill:
```
/github-plugin-installer https://github.com/sfc-gh-kweinerman/snowhouse-health-check
```

---

## Running a Health Check

### What you need to know about the customer

| Input | Where to find it | Example |
|-------|-----------------|---------|
| Account locator | Snowsight → Account → Account Info, or Salesforce | `QLB30703` |
| Deployment | Same sources (listed as region/cloud on the account) | `va2`, `va3`, `prod3` |
| Credit rate | Salesforce opportunity / contract | `3.44` |
| Company name | Salesforce | `"Acme Corp"` |
| Warehouse names | Optional — ask the customer, or skip and use entity IDs | `{"123456": "ETL_WH"}` |

**Deployment quick reference:**
- `va2` = AWS US East (most accounts)
- `va3` = AWS US East (newer accounts)
- `prod3` = AWS US West
- `eu-west-1` = AWS EU Ireland
- Ask your SE if unsure — or just try `va2` first

### How to run it

Just ask CoCo in plain language:
```
Run a health check for Acme Corp. Account locator is QLB30703, deployment va3,
credit rate $3.44. Their top warehouses are ETL_WH (entity 509876830477),
REPORTING_WH (entity 1991706576), and DEVELOPER_WH (entity 771234567).
```

CoCo will:
1. Look up the account in Snowhouse to confirm it exists
2. Show you the warehouse entities it found and ask you to confirm
3. Generate the report (takes ~2–3 minutes — it pulls 12 months + 90-day hourly data)
4. Save the PDF to your working directory and tell you the path

### Skipping the warehouse name mapping

If you don't have the warehouse names, that's fine — just say so. The report will use entity IDs as labels, and the PDF will still have all the analysis. The customer can tell you the names later if they want a revised version.

---

## Manual Command-Line Usage

### Step 1: Look up the account
```bash
SNOWFLAKE_CONNECTION_NAME=SNOWHOUSE_AWS_US_WEST_2 \
  uv run --project ~/.snowflake/cortex/skills/snowhouse-health-check \
  python ~/.snowflake/cortex/skills/snowhouse-health-check/scripts/lookup_account.py \
  --locator QLB30703 --deployment va3
```

This returns the account ID, creation date, and top warehouse entity IDs with 3-month credit totals.

### Step 2: Build the warehouse map (optional)
Create a JSON file mapping entity IDs to names:
```json
{"509876830477": "ETL_WH", "1991706576": "REPORTING_WH"}
```

### Step 3: Generate the report
```bash
SNOWFLAKE_CONNECTION_NAME=SNOWHOUSE_AWS_US_WEST_2 \
  uv run --project ~/.snowflake/cortex/skills/snowhouse-health-check \
  python ~/.snowflake/cortex/skills/snowhouse-health-check/scripts/generate_report.py \
  --account-id 7780103 \
  --deployment va3 \
  --credit-rate 3.44 \
  --company-name "Acme Corp" \
  --locator QLB30703 \
  --warehouse-map '{"509876830477":"ETL_WH","1991706576":"REPORTING_WH"}' \
  --output "./Acme_Health_Check_July2026.pdf"
```

All arguments except `--warehouse-map` and `--output` are required.

---

## FAQ

**Q: I get a browser pop-up asking me to log in with Okta.**  
A: That's expected on first use. Complete the Okta auth and the script continues automatically.

**Q: I get "Account not found" for a locator I know is real.**  
A: The deployment might be wrong. Try `va2`, then `va3`, then `prod3`. Some edge cases: EU accounts use `eu-west-1`, Asia-Pacific use `ap-southeast-2`.

**Q: The report uses entity IDs instead of warehouse names.**  
A: You didn't provide a warehouse map, or the entity ID isn't in the map. Run `lookup_account.py` first — it shows entity IDs with 3-month credit totals. Then ask the customer to match IDs to names, or check with their admin.

**Q: Can I re-run with different settings without re-pulling data?**  
A: Not directly — each run fetches from Snowhouse fresh. Runs are fast enough (~2 min) that this is fine.

**Q: The PDF has "WH_481103643173" — what's that?**  
A: An entity ID that wasn't in your warehouse map. Ask the customer which warehouse that is and re-run with the updated map.

**Q: What if the customer has multiple accounts?**  
A: Run `lookup_account.py` for each locator. Generate separate PDFs and note which account each covers. The cover page includes the locator and deployment for reference.

**Q: Can I change the analysis window?**  
A: Yes — add `--months-back 6` (or any number) to the generate command to limit the monthly history. The hourly analysis is always last 90 days.

---

## Updating

```bash
cd ~/.snowflake/cortex/skills/snowhouse-health-check && git pull
```

Or restart CoCo and use the Sync button on the skill card in Settings → Skills.

---

## Questions / Issues

Slack: **#ae-tools** or ping **@kweinerman**
