---
name: snowhouse-health-check
description: "Generate customer Snowflake cost health check PDFs from Snowhouse metering data. Use for ALL requests involving: health check, account health, cost review, PDF report, customer spend analysis, metering report, cost breakdown for a customer account, SE health check, QBR prep, EBR prep. Requires account locator and deployment. NOT for querying your own account (use cost-intelligence instead)."
---

# Snowhouse Health Check

Generate a comprehensive cost health check PDF for any Snowflake customer account using Snowhouse internal metering data.

## Prerequisites

- Connection: `SNOWHOUSE_AWS_US_WEST_2` (Snowhouse access)
- User provides: account locator + deployment (e.g., `QLB30703` in `va3`)

## Workflow

### Step 1: Gather Account Information

**Ask user for:**
1. **Account locator** (e.g., QLB30703, KPA93530, BBB14112)
2. **Deployment** (e.g., va2, va3, prod3, us-west-2, etc.)

### Step 2: Look Up Account in Snowhouse

**Run:**
```bash
SNOWFLAKE_CONNECTION_NAME=SNOWHOUSE_AWS_US_WEST_2 uv run --project <SKILL_DIR> python <SKILL_DIR>/scripts/lookup_account.py --locator <LOCATOR> --deployment <DEPLOYMENT>
```

This returns:
- Account ID (the `id` field used to filter metering tables)
- Account creation date
- Company name (if available)
- List of warehouse entity IDs with recent credit totals

**If lookup fails:** Verify the locator and deployment are correct. Common deployments: va2, va3, prod3, us-east-1, us-west-2, eu-west-1, ap-southeast-2.

### Step 3: Gather Additional Inputs

**Ask user for:**
1. **Credit rate** ($/credit) — e.g., 1.78, 3.00, 3.44
2. **Company name** (for PDF cover) — use Snowhouse value if available, otherwise ask
3. **Warehouse name mapping** (optional) — entity_id to warehouse name pairs. If not provided, the report will use entity IDs.

Save warehouse mapping as a JSON file:
```json
{"509876830477": "SMARTSPOT_WAREHOUSE", "1991706576": "ONESOURCE_WAREHOUSE"}
```

**⚠️ MANDATORY STOPPING POINT**: Confirm all inputs before generating report.

### Step 4: Generate Report

**Run:**
```bash
SNOWFLAKE_CONNECTION_NAME=SNOWHOUSE_AWS_US_WEST_2 uv run --project <SKILL_DIR> python <SKILL_DIR>/scripts/generate_report.py \
  --account-id <ID> \
  --deployment <DEPLOYMENT> \
  --credit-rate <RATE> \
  --company-name "<NAME>" \
  --warehouse-map <JSON_FILE> \
  --output "<OUTPUT_PATH>.pdf"
```

Default output path: `./<company_name>_Health_Check_<Month><Year>.pdf`

The script:
1. Pulls all metering data (warehouse, clustering, tasks, cloud services, AI, storage)
2. Auto-detects notable patterns (clustering spikes, warehouse anomalies, AI adoption)
3. Generates 7 charts (stacked monthly, warehouse trends, clustering, AI, daily, storage, entities)
4. Assembles 8-page PDF with recommendations

### Step 5: Present Output

Confirm PDF was generated and provide path. Summarize key findings:
- Total credits and estimated annual run rate
- Top optimization opportunity
- Notable trends or spikes

## Tools

### Script: lookup_account.py

**Description**: Resolves account locator to Snowhouse account ID, discovers warehouse entities.

**Usage:**
```bash
SNOWFLAKE_CONNECTION_NAME=SNOWHOUSE_AWS_US_WEST_2 uv run --project <SKILL_DIR> python <SKILL_DIR>/scripts/lookup_account.py \
  --locator <ACCOUNT_LOCATOR> \
  --deployment <DEPLOYMENT>
```

**Output:** JSON with account_id, created_on, company_name, warehouse_entities

### Script: generate_report.py

**Description**: Pulls Snowhouse metering data and generates health check PDF.

**Usage:**
```bash
SNOWFLAKE_CONNECTION_NAME=SNOWHOUSE_AWS_US_WEST_2 uv run --project <SKILL_DIR> python <SKILL_DIR>/scripts/generate_report.py \
  --account-id <ID> \
  --deployment <DEPLOYMENT> \
  --credit-rate <RATE> \
  --company-name "<NAME>" \
  --warehouse-map <JSON_FILE_OR_INLINE_JSON> \
  --output "<PATH>.pdf"
```

**Arguments:**
- `--account-id` (required): Snowhouse account ID (the `id` field from ACCOUNT_ETL_V)
- `--deployment` (required): Deployment name for table prefix (va2, va3, prod3, etc.)
- `--credit-rate` (required): Dollar cost per credit
- `--company-name` (required): Company name for PDF branding
- `--warehouse-map` (optional): Path to JSON file mapping entity_id → warehouse name
- `--output` (optional): Output PDF path (default: ./<company>_Health_Check.pdf)
- `--months-back` (optional): Analysis window in months (default: 12)

## Stopping Points

- ✋ After Step 2: Confirm account was found correctly
- ✋ After Step 3: Confirm all inputs before generating
- ✋ After Step 4: If script errors, troubleshoot before retrying

## Output

8-page PDF containing:
1. Cover page with company branding and key highlights
2. Executive summary with KPI tiles and monthly stacked bar chart
3. Warehouse analysis with sizing recommendations
4. Clustering/serverless deep-dive (if clustering credits detected)
5. AI & Cortex adoption (if AI credits detected)
6. Infrastructure & storage patterns
7. Prioritized optimization recommendations with dollar estimates
8. Appendix with full monthly data tables

## Troubleshooting

**Error: Account not found**
- Check locator spelling (case-sensitive, usually uppercase)
- Verify deployment — try SHOW SCHEMAS IN DATABASE METERING_BY_HOUR_<deployment>

**Error: No metering data**
- Account may be new or inactive
- Try a shorter --months-back window

**Error: SSO authentication prompt**
- Normal — complete Okta login in browser, script continues automatically

**Error: Storage double-counting**
- Load `references/snowhouse-tables.md` for the entity_id=0 gotcha
