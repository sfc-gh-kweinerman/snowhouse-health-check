#!/usr/bin/env python3
"""
lookup_account.py — Resolve account locator to Snowhouse account ID and discover warehouse entities.

Usage:
    SNOWFLAKE_CONNECTION_NAME=SNOWHOUSE_AWS_US_WEST_2 uv run --project <SKILL_DIR> \
        python <SKILL_DIR>/scripts/lookup_account.py --locator <LOCATOR> --deployment <DEPLOYMENT>
"""

import argparse
import decimal
import json
import os
import sys

import snowflake.connector


def get_conn():
    return snowflake.connector.connect(
        connection_name=os.getenv("SNOWFLAKE_CONNECTION_NAME") or "SNOWHOUSE_AWS_US_WEST_2"
    )


def query(conn, sql):
    cur = conn.cursor()
    cur.execute(sql)
    cols = [d[0] for d in cur.description]
    rows = cur.fetchall()
    results = []
    for row in rows:
        record = {}
        for col, val in zip(cols, row):
            if isinstance(val, decimal.Decimal):
                val = float(val)
            record[col] = val
        results.append(record)
    return results


def main():
    parser = argparse.ArgumentParser(description="Look up Snowflake account in Snowhouse")
    parser.add_argument("--locator", required=True, help="Account locator (e.g., QLB30703)")
    parser.add_argument("--deployment", required=True, help="Deployment (e.g., va2, va3, prod3)")
    args = parser.parse_args()

    dep = args.deployment.upper()
    locator = args.locator.upper()

    conn = get_conn()

    print(f"Looking up account {locator} in {dep}...")
    account_rows = query(conn, f"""
        SELECT id, account_id, name, company_name, created_on, state
        FROM SNOWHOUSE_VIEWS.{dep}.ACCOUNT_ETL_V
        WHERE name = '{locator}'
        LIMIT 1
    """)

    if not account_rows:
        print(f"ERROR: Account '{locator}' not found in SNOWHOUSE_VIEWS.{dep}.ACCOUNT_ETL_V")
        sys.exit(1)

    acct = account_rows[0]
    account_id = acct["ID"]
    print(f"\nAccount found:")
    print(f"  ID (for metering filter): {account_id}")
    print(f"  Name: {acct['NAME']}")
    print(f"  Company: {acct.get('COMPANY_NAME') or '(not set)'}")
    print(f"  Created: {acct.get('CREATED_ON')}")
    print(f"  State: {acct.get('STATE')}")

    print(f"\nDiscovering warehouse entities (last 3 months)...")
    wh_rows = query(conn, f"""
        SELECT entity_id, SUM(credits) AS credits
        FROM METERING_BY_HOUR_{dep}.METERING.WAREHOUSE_METERING
        WHERE account_id = {account_id}
          AND usage_time >= DATEADD('month', -3, CURRENT_DATE())
        GROUP BY entity_id
        ORDER BY credits DESC
    """)

    if wh_rows:
        print(f"\n  {'Entity ID':<20} {'Credits (3-mo)':<16} Notes")
        print(f"  {'-'*20} {'-'*16} {'-'*20}")
        for row in wh_rows:
            eid = row["ENTITY_ID"]
            cr = row["CREDITS"]
            print(f"  {eid:<20} {cr:>12,.1f}    (needs name mapping)")
    else:
        print("  No warehouse metering data found in last 3 months.")

    print(f"\nChecking for clustering credits...")
    clust_rows = query(conn, f"""
        SELECT SUM(credits) AS credits
        FROM METERING_BY_HOUR_{dep}.METERING.COMPUTE_SERVICE_METERING
        WHERE account_id = {account_id}
          AND event_type = 'COMPUTE_SERVICE_CLUSTERING'
          AND usage_time >= DATEADD('month', -3, CURRENT_DATE())
    """)
    clust_credits = clust_rows[0]["CREDITS"] if clust_rows and clust_rows[0]["CREDITS"] else 0
    print(f"  Clustering credits (last 3 months): {clust_credits:,.1f}")

    print(f"\nChecking for AI/Cortex usage...")
    ai_rows = query(conn, f"""
        SELECT SUM(credits) AS credits
        FROM METERING_BY_HOUR_{dep}.METERING.AI_SERVICES_METERING
        WHERE account_id = {account_id}
          AND usage_time >= DATEADD('month', -3, CURRENT_DATE())
    """)
    ai_credits = ai_rows[0]["CREDITS"] if ai_rows and ai_rows[0]["CREDITS"] else 0
    print(f"  AI/Cortex credits (last 3 months): {ai_credits:,.1f}")

    conn.close()

    output = {
        "account_id": account_id,
        "locator": locator,
        "deployment": dep,
        "company_name": acct.get("COMPANY_NAME") or "",
        "created_on": str(acct.get("CREATED_ON")),
        "warehouse_entities": {str(r["ENTITY_ID"]): r["CREDITS"] for r in wh_rows},
        "clustering_credits_3mo": clust_credits,
        "ai_credits_3mo": ai_credits,
    }
    print(f"\n--- JSON OUTPUT ---")
    print(json.dumps(output, indent=2, default=str))


if __name__ == "__main__":
    main()
