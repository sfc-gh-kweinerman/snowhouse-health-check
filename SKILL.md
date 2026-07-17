---
name: snowhouse-health-check
description: "Generate a deep-dive customer Snowflake cost & efficiency health check PDF from Snowhouse metering data. Covers warehouse sizing, multi-cluster candidacy, overnight/weekend activity audit, per-warehouse recommendations, and query-level SQL scripts. Requires account locator and deployment. NOT for querying your own account (use cost-intelligence instead)."
---

# Snowhouse Health Check

Generate a comprehensive 14-page cost and efficiency health check PDF for any Snowflake customer account using Snowhouse internal metering data.

## Prerequisites

- **CoCo connection**: `SNOWHOUSE_AWS_US_WEST_2` (Snowhouse access via Okta SSO)
- **Account locator + deployment** from the user (e.g., `QLB30703` in `va3`)
- **uv** installed on the user's machine (`brew install uv`)

---

## Workflow

### Step 1: Gather Account Info

Ask the user for:
1. **Account locator** (e.g., `QLB30703`) — from Snowsight or Salesforce
2. **Deployment** (e.g., `va2`, `va3`, `prod3`) — from account info or Salesforce
3. **Credit rate** — $/credit from the contract in Salesforce
4. **Company name** — for PDF branding
5. **Warehouse names** (optional) — entity_id → name mapping; can be skipped

**Deployment quick reference:**
- `va2` = AWS US East (most accounts)
- `va3` = AWS US East (newer)
- `prod3` = AWS US West
- `eu-west-1` = AWS EU
- `ap-southeast-2` = AWS Asia-Pacific

---

### Step 2: Look Up Account in Snowhouse

```bash
SNOWFLAKE_CONNECTION_NAME=SNOWHOUSE_AWS_US_WEST_2 uv run --project <SKILL_DIR> python <SKILL_DIR>/scripts/lookup_account.py --locator <LOCATOR> --deployment <DEPLOYMENT>
```

This returns:
- **Account ID** (the `id` field — needed for the report command)
- Account creation date
- List of warehouse entity IDs with 3-month credit totals

**If lookup fails:** Try alternate deployments. For US accounts: `va2` → `va3` → `prod3`. For EU: `eu-west-1`.

**✋ STOP**: Confirm the account was found correctly before proceeding. Show the user the company name, creation date, and entity list.

---

### Step 3: Build Warehouse Map (Optional but Recommended)

From the entity list in Step 2, ask the user to provide warehouse names, or let them provide a JSON mapping:

```json
{"509876830477": "ETL_WH", "1991706576": "REPORTING_WH", "771234567": "DEVELOPER_WH"}
```

Save this to a temp file: `/tmp/<company>_wh_map.json`

If the user doesn't have names yet, that's OK — entity IDs will be used as labels and the report is still fully functional.

**Where to get warehouse names:** Ask the customer's Snowflake admin, or check Snowsight → Admin → Warehouses.

---

### Step 4: Confirm Inputs Before Generating

**✋ MANDATORY STOP**: Before running generate_report.py, confirm all inputs with the user:

```
About to generate health check:
  Company:     <name>
  Account:     <locator> (<account_id>) in <deployment>
  Credit rate: $<rate>/credit
  Warehouses:  <N named, M using entity IDs>
  Output:      ./<company>_Health_Check_<Month><Year>.pdf

Ready to generate? (takes ~2–3 minutes)
```

---

### Step 5: Generate Report

```bash
SNOWFLAKE_CONNECTION_NAME=SNOWHOUSE_AWS_US_WEST_2 uv run --project <SKILL_DIR> python <SKILL_DIR>/scripts/generate_report.py \
  --account-id <ID> \
  --deployment <DEPLOYMENT> \
  --credit-rate <RATE> \
  --company-name "<NAME>" \
  --warehouse-map '<JSON_OR_FILE_PATH>' \
  --locator <LOCATOR> \
  --output "<OUTPUT_PATH>.pdf"
```

Default output path if `--output` is omitted: `./<company_name>_Health_Check.pdf`

The script:
1. Pulls 12 months of monthly metering (warehouse, clustering, AI, cloud services, tasks, storage)
2. Pulls 90 days of hourly warehouse data for pattern analysis
3. Analyzes sizing, multi-cluster candidacy, overnight/weekend activity per warehouse
4. Generates 9 charts (heatmap, box plot, weekend/overnight, bar, monthly stacked, daily, storage, clustering, AI)
5. Assembles 14-page PDF with recommendations and SQL scripts

**✋ STOP**: If the script errors, troubleshoot before retrying (see Troubleshooting below).

---

### Step 6: Present Output

Confirm the PDF was saved and provide the path. Summarize key findings from the report:

1. **YTD credits and annualized run rate**
2. **Top warehouse** by 90-day spend
3. **Most impactful recommendation** from the priority matrix
4. **Any anomalies** — high overnight activity, ALATION_WH-style always-on patterns, clustering spikes

---

## Report Contents (14 Pages)

| Page | Section | What It Shows |
|------|---------|---------------|
| 1 | Cover | Company, account, region, credit rate, analysis window |
| 2 | Executive Summary | KPI tiles + 12-month stacked bar + monthly table |
| 3 | Warehouse Breakdown | 90-day horizontal bar + stats table (max hr, active hrs/day, trend) |
| 4 | Hourly Activity Patterns | Heatmap: warehouse × UTC hour — shows scheduled vs. interactive |
| 5 | Weekend & Overnight Audit | Dual chart + table with action flags |
| 6 | Sizing & Multi-Cluster | Box plot + sizing table with CV, peak size, typical size |
| 7–8 | Per-Warehouse Recs | Metric-driven sizing, multi-cluster, auto-suspend guidance per warehouse |
| 9–11 | SQL Scripts | 6 ready-to-run queries for query-level investigation |
| 12 | Priority Action Matrix | Data-driven top-5 warehouse actions + 2 generic rows |
| 13 | AI & Cortex (conditional) | Only included if AI usage > 1 credit |
| 14 | Infrastructure + Appendix | Daily chart, storage, full warehouse appendix |

---

## Troubleshooting

**"Account not found"**
- Check locator spelling (case-sensitive, usually uppercase)
- Try other deployments: `va2` → `va3` → `prod3` → `us-east-1`
- Run: `SHOW SCHEMAS IN DATABASE METERING_BY_HOUR_VA2;` in Snowhouse to see what's available

**SSO authentication pop-up**
- Normal on first use. Complete Okta login in the browser — the script continues automatically after auth.

**"No metering data" or very short report**
- Account may be very new or inactive
- Try `--months-back 3` to shorten the window

**Warehouse shows as entity ID (e.g., WH_481103643173) in the report**
- Entity ID was not in the warehouse map
- Ask the customer which warehouse corresponds to that ID and re-run with updated `--warehouse-map`

**Script takes more than 5 minutes**
- Normal for accounts with many warehouses or long history
- The hourly data pull (90 days × N warehouses) is the slow step

---

## Tools

### lookup_account.py
Resolves account locator → Snowhouse account ID. Also discovers warehouse entities with 3-month credits.

```bash
SNOWFLAKE_CONNECTION_NAME=SNOWHOUSE_AWS_US_WEST_2 uv run --project <SKILL_DIR> \
  python <SKILL_DIR>/scripts/lookup_account.py \
  --locator <LOCATOR> --deployment <DEPLOYMENT>
```

### generate_report.py
Pulls all metering data and generates the PDF.

| Argument | Required | Description |
|----------|----------|-------------|
| `--account-id` | Yes | From lookup_account.py output |
| `--deployment` | Yes | va2, va3, prod3, etc. |
| `--credit-rate` | Yes | $/credit from contract |
| `--company-name` | Yes | For PDF branding |
| `--locator` | No | For cover page display |
| `--warehouse-map` | No | JSON file path or inline JSON |
| `--output` | No | PDF path (default: ./<company>_Health_Check.pdf) |
| `--months-back` | No | Monthly history window (default: 12) |
