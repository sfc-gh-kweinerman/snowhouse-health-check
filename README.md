# Snowhouse Health Check Skill

A Cortex Code skill that generates customer Snowflake cost health check PDFs from Snowhouse internal metering data. Built for SEs preparing QBR/EBR deliverables.

## What It Does

Given an account locator and deployment, this skill:

1. Looks up the customer in Snowhouse
2. Pulls 12 months of metering data (warehouse, clustering, tasks, cloud services, AI/Cortex, storage)
3. Generates a branded 8-page PDF with charts, cost breakdowns, and optimization recommendations
4. All dollar amounts calculated using the customer's credit rate

## Install

```bash
git clone https://github.com/sfc-gh-kweinerman/snowhouse-health-check.git ~/.snowflake/cortex/skills/snowhouse-health-check
```

Restart Cortex Code (or open a new session). The skill auto-activates on phrases like:
- "run a health check for account XYZ"
- "cost review for customer ABC in va3"
- "generate a PDF report for account BBB14112 in prod3"

## What You'll Need

| Input | Example | Notes |
|-------|---------|-------|
| Account locator | `QLB30703` | From Snowsight or Salesforce |
| Deployment | `va3` | va2, va3, prod3, etc. |
| Credit rate | `1.78` | $/credit from the contract |
| Company name | `BMG360` | For PDF branding |
| Warehouse mapping | `{entity_id: name}` | Optional — the skill discovers entity IDs and you provide names |

## What You Get

An 8-page PDF containing:

- **Cover** — Company branding, account info, key highlights
- **Executive Summary** — KPI tiles, monthly stacked bar chart, run rate
- **Warehouse Analysis** — Top 8 trend lines, per-warehouse table with sizing recommendations
- **Clustering Deep-Dive** — Only included if clustering credits detected; shows monthly spend, top table entities, remediation SQL
- **AI & Cortex Adoption** — Only included if AI usage exists; stacked bar by product
- **Infrastructure & Storage** — Daily compute chart, storage volatility
- **Optimization Recommendations** — Prioritized actions with estimated dollar savings
- **Appendix** — Full monthly tables, warehouse entity mapping

## Prerequisites

- Snowhouse access via `SNOWHOUSE_AWS_US_WEST_2` connection
- Python 3.11+ (uv handles dependency installation automatically)
- [uv](https://docs.astral.sh/uv/) installed (`brew install uv` or `pip install uv`)

## Manual Usage (outside Cortex Code)

```bash
# Step 1: Look up the account
SNOWFLAKE_CONNECTION_NAME=SNOWHOUSE_AWS_US_WEST_2 uv run --project ~/.snowflake/cortex/skills/snowhouse-health-check \
  python ~/.snowflake/cortex/skills/snowhouse-health-check/scripts/lookup_account.py \
  --locator QLB30703 --deployment va3

# Step 2: Generate the report
SNOWFLAKE_CONNECTION_NAME=SNOWHOUSE_AWS_US_WEST_2 uv run --project ~/.snowflake/cortex/skills/snowhouse-health-check \
  python ~/.snowflake/cortex/skills/snowhouse-health-check/scripts/generate_report.py \
  --account-id 7780103 \
  --deployment va3 \
  --credit-rate 1.78 \
  --company-name "BMG360" \
  --locator QLB30703 \
  --warehouse-map '{"509876830477":"SMARTSPOT_WAREHOUSE","1991706576":"ONESOURCE_WAREHOUSE"}' \
  --output "./BMG360_Health_Check.pdf"
```

## Known Gotchas

- **SSO auth pop-up**: First run requires Okta browser authentication. This is normal.
- **Warehouse names**: Snowhouse only has entity IDs — you need the customer or their admin to provide the mapping.
- **Clustering entity IDs**: These are table-level IDs (not warehouse). Can't resolve to table names from Snowhouse alone.
- **Storage double-counting**: The skill handles this correctly (filters entity_id=0 aggregate rows).

## Updating

```bash
cd ~/.snowflake/cortex/skills/snowhouse-health-check && git pull
```
