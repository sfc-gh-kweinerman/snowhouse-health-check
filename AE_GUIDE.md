# AE Guide: Snowflake Customer Health Check

> **What this is:** A Cortex Code skill that generates a 14-page cost and efficiency health check PDF for any Snowflake customer, directly from Snowhouse metering data. No SE needed.

---

## One-Time Setup (15 minutes)

Do this once. After setup, generating a health check for any customer takes ~5 minutes.

### Step 1 — Install Cortex Code Desktop
Download from **go/coco** (internal Snowflake). Install and sign in with your Snowflake account.

### Step 2 — Install uv
Open Terminal and run:
```bash
brew install uv
```
(Windows: `pip install uv`)

### Step 3 — Configure Snowhouse Connection in CoCo
1. Open CoCo → Settings (gear icon) → Connections
2. Add a new connection named exactly: **`SNOWHOUSE_AWS_US_WEST_2`**
3. Fill in the Snowhouse account credentials (ask your SE or #snowhouse-users if you don't have these)
4. Auth type: **Externalbrowser** (uses your Okta SSO — no password needed)

### Step 4 — Install the Skill
In Terminal:
```bash
git clone https://github.com/sfc-gh-kweinerman/snowhouse-health-check.git \
  ~/.snowflake/cortex/skills/snowhouse-health-check
```
Then restart CoCo (quit and reopen).

**That's it.** You're ready to generate health checks.

---

## Running a Health Check

### What you need from the customer / Salesforce

| What | Where to get it | Example |
|------|----------------|---------|
| Account locator | Salesforce → Account → Snowflake Account field, OR Snowsight → bottom-left account menu | `QLB30703` |
| Deployment | Same as above (listed as region) | `va2` |
| Credit rate | Salesforce opportunity / contract | `3.44` |
| Company name | Salesforce | `Acme Corp` |
| Warehouse names | Ask the customer's admin, or skip | `{"509876830477": "ETL_WH"}` |

**Not sure about the deployment?** Start with `va2`. If that doesn't work, try `va3`, then `prod3`.

---

### In CoCo, just tell it what you know

Open a CoCo session (workspace or playground) and type something like:

```
Run a health check for Acme Corp.
Account locator: QLB30703
Deployment: va3
Credit rate: $3.44
Company: Acme Corp
Warehouse names: ETL_WH (509876830477), REPORTING_WH (1991706576)
```

CoCo will:
1. Look up the account in Snowhouse (~10 seconds)
2. Show you the entity list and ask you to confirm
3. Generate the report (~2–3 minutes — it pulls a lot of data)
4. Tell you where the PDF was saved

**Don't have warehouse names?** Just omit them. The report still works — it uses entity IDs as labels and all analysis is fully functional. You can always re-run with names later.

---

## What the PDF Covers

The output is a **14-page deep-dive**, not a surface-level cost summary. Key sections:

### Warehouse Sizing (pages 6–8)
Shows whether each warehouse is sized correctly for its actual workload. Uses hourly credit patterns — not just totals — to catch the "big warehouse idle" problem where a customer sized up to handle rare spikes but pays for that size 24/7.

### Multi-Cluster Candidacy (page 6)
Identifies warehouses with bursty, concurrent query patterns that would benefit from multi-cluster auto-scaling instead of a larger single warehouse.

### Overnight & Weekend Audit (page 5)
Flags warehouses burning credits at unexpected times — often BI tools keeping warehouses warm, ETL schedules that are too broad, or AUTO_SUSPEND set too high.

### SQL Scripts (pages 9–11)
Six ready-to-run queries the customer can run in their own Snowflake account to find expensive queries, repetitive patterns, and full table scans. Hands the customer actionable homework without you needing account access.

### Priority Action Matrix (page 12)
Numbered list of specific actions for the top-5 warehouses, derived from the actual data. Not generic advice — specific to what their usage patterns show.

---

## Sharing the PDF

The PDF is marked **"Confidential — Prepared by Snowflake Account Team"** and includes the customer's account/region on the cover. It's ready to send as-is.

Good use cases:
- **QBR/EBR prep** — attach to your deck or share as a standalone deliverable
- **Renewal conversations** — shows you've done the homework on optimization
- **Post-onboarding review** — 90 days after go-live to catch early inefficiencies
- **Proactive outreach** — "I ran your numbers and found something interesting"

---

## Common Issues

**"Account not found"**
The deployment is wrong. Try `va2`, then `va3`, then `prod3`. EU accounts use `eu-west-1`.

**Browser login pop-up appears**
Normal on first use. Complete your Okta login — the report generation continues automatically after.

**PDF has "WH_123456789" instead of a warehouse name**
You didn't include that entity ID in the warehouse map. Ask the customer which warehouse it is and re-run.

**Script runs for more than 5 minutes**
Still normal for large accounts — it's pulling 90 days of hourly data. Let it run.

**CoCo doesn't seem to recognize the skill**
Restart CoCo after installing the skill. If still not working, check that the folder is at `~/.snowflake/cortex/skills/snowhouse-health-check` (exact path matters).

---

## Updating the Skill

When a new version is released, run:
```bash
cd ~/.snowflake/cortex/skills/snowhouse-health-check && git pull
```
Then restart CoCo.

---

## Get Help

- **Slack**: `#ae-tools` or DM `@kweinerman`
- **GitHub**: https://github.com/sfc-gh-kweinerman/snowhouse-health-check (open an issue for bugs)
