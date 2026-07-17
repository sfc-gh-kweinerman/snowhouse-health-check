#!/usr/bin/env python3
"""
generate_report.py — Pull Snowhouse metering data and generate a deep-dive cost
health check PDF. Includes hourly pattern analysis, sizing/multi-cluster
candidacy, weekend/overnight audit, per-warehouse recommendations, and
SQL scripts for query-level investigation.

Usage:
    SNOWFLAKE_CONNECTION_NAME=SNOWHOUSE_AWS_US_WEST_2 uv run --project <SKILL_DIR> \\
        python <SKILL_DIR>/scripts/generate_report.py \\
        --account-id 7341059 \\
        --deployment va2 \\
        --credit-rate 3.6 \\
        --company-name "Cresset Capital" \\
        --warehouse-map warehouse_map.json \\
        --output "Cresset_Health_Check.pdf"
"""

import argparse
import decimal
import io
import json
import os
import warnings
warnings.filterwarnings("ignore")

import snowflake.connector
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import matplotlib.dates as mdates
from datetime import datetime

from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    Image, HRFlowable, PageBreak
)

# ── COLORS & PALETTE ──────────────────────────────────────────────────────────
SF_BLUE  = colors.Color(0.043, 0.333, 0.698)
SF_CYAN  = colors.Color(0.0,   0.749, 0.925)
SF_GREY  = colors.Color(0.933, 0.933, 0.933)
SF_DARK  = colors.Color(0.133, 0.133, 0.133)

PALETTE = ["#0757B2", "#00BEec", "#25B57C", "#F99A0F", "#DB3030",
           "#8A4EAE", "#5B9BD5", "#70AD47", "#FF7043", "#AB47BC",
           "#26C6DA", "#EC407A", "#78909C", "#66BB6A", "#FFA726"]

AI_FRIENDLY = {
    "AI_SERVICE_CORTEX_SEARCH":                             "Cortex Search",
    "AI_SERVICE_CORTEX_ANALYST_MESSAGE":                    "Cortex Analyst",
    "AI_SERVICE_CORTEX_FUNCTION_UNSEGMENTED_TOKENS":        "Cortex Functions",
    "AI_SERVICE_CORTEX_FUNCTION_INPUT_TOKENS":              "Cortex Functions",
    "AI_SERVICE_CORTEX_FUNCTION_OUTPUT_TOKENS":             "Cortex Functions",
    "AI_SERVICE_CORTEX_FUNCTION":                           "Cortex Functions",
    "AI_SERVICE_AGENT_INPUT_TOKENS":                        "Cortex Agents",
    "AI_SERVICE_AGENT_OUTPUT_TOKENS":                       "Cortex Agents",
    "AI_SERVICE_AGENT_CACHE_WRITE_TOKENS":                  "Cortex Agents",
    "AI_SERVICE_AGENT_CACHE_READ_TOKENS":                   "Cortex Agents",
    "AI_SERVICE_AGENT_ANALYST_INPUT_TOKENS":                "Cortex Agents",
    "AI_SERVICE_AGENT_ANALYST_OUTPUT_TOKENS":               "Cortex Agents",
    "AI_SERVICE_SNOWFLAKE_INTELLIGENCE_INPUT_TOKENS":       "Snowflake Intelligence",
    "AI_SERVICE_SNOWFLAKE_INTELLIGENCE_OUTPUT_TOKENS":      "Snowflake Intelligence",
    "AI_SERVICE_SNOWFLAKE_INTELLIGENCE_CACHE_WRITE_TOKENS": "Snowflake Intelligence",
    "AI_SERVICE_SNOWFLAKE_INTELLIGENCE_CACHE_READ_TOKENS":  "Snowflake Intelligence",
    "AI_SERVICE_CORTEX_DOCUMENT_FUNCTION":                  "Document AI",
    "AI_SERVICE_CORTEX_FINETUNING":                         "Fine-Tuning",
}


# ── CONNECTION ─────────────────────────────────────────────────────────────────
def get_conn():
    return snowflake.connector.connect(
        connection_name=os.getenv("SNOWFLAKE_CONNECTION_NAME") or "SNOWHOUSE_AWS_US_WEST_2"
    )


def qdf(conn, sql):
    cur = conn.cursor()
    cur.execute(sql)
    cols = [d[0] for d in cur.description]
    rows = cur.fetchall()
    df = pd.DataFrame(rows, columns=cols)
    for col in df.columns:
        if df[col].apply(lambda x: isinstance(x, decimal.Decimal)).any():
            df[col] = df[col].apply(lambda x: float(x) if isinstance(x, decimal.Decimal) else x)
    return df


# ── DATA PULLS ─────────────────────────────────────────────────────────────────
def pull_data(conn, account_id, deployment, months_back):
    """Pull monthly-granularity metering data for the analysis window."""
    dep = deployment.upper()
    db  = f"METERING_BY_HOUR_{dep}.METERING"

    print("Pulling warehouse metering (monthly)...")
    wh = qdf(conn, f"""
        SELECT entity_id, DATE_TRUNC('month', usage_time) AS month, SUM(credits) AS credits
        FROM {db}.WAREHOUSE_METERING
        WHERE account_id = {account_id}
          AND usage_time >= DATEADD('month', -{months_back}, CURRENT_DATE())
        GROUP BY 1, 2
    """)
    wh["MONTH"] = pd.to_datetime(wh["MONTH"]).dt.tz_localize(None)

    print("Pulling clustering metering...")
    clust = qdf(conn, f"""
        SELECT entity_id, DATE_TRUNC('month', usage_time) AS month, SUM(credits) AS credits
        FROM {db}.COMPUTE_SERVICE_METERING
        WHERE account_id = {account_id} AND event_type = 'COMPUTE_SERVICE_CLUSTERING'
          AND usage_time >= DATEADD('month', -{months_back}, CURRENT_DATE())
        GROUP BY 1, 2
    """)
    clust["MONTH"] = pd.to_datetime(clust["MONTH"]).dt.tz_localize(None)

    print("Pulling scheduled tasks metering...")
    tasks = qdf(conn, f"""
        SELECT DATE_TRUNC('month', usage_time) AS month, SUM(credits) AS credits
        FROM {db}.COMPUTE_SERVICE_METERING
        WHERE account_id = {account_id} AND event_type = 'COMPUTE_SERVICE_USER_SCHEDULED_TASK'
          AND usage_time >= DATEADD('month', -{months_back}, CURRENT_DATE())
        GROUP BY 1
    """)
    tasks["MONTH"] = pd.to_datetime(tasks["MONTH"]).dt.tz_localize(None)

    print("Pulling cloud services metering...")
    gs = qdf(conn, f"""
        SELECT DATE_TRUNC('month', usage_time) AS month, SUM(credits) AS credits
        FROM {db}.GS_METERING
        WHERE account_id = {account_id}
          AND usage_time >= DATEADD('month', -{months_back}, CURRENT_DATE())
        GROUP BY 1
    """)
    gs["MONTH"] = pd.to_datetime(gs["MONTH"]).dt.tz_localize(None)

    print("Pulling AI/Cortex metering...")
    ai = qdf(conn, f"""
        SELECT event_type, DATE_TRUNC('month', usage_time) AS month, SUM(credits) AS credits
        FROM {db}.AI_SERVICES_METERING
        WHERE account_id = {account_id}
          AND usage_time >= DATEADD('month', -{months_back}, CURRENT_DATE())
        GROUP BY 1, 2
    """)
    ai["service"] = ai["EVENT_TYPE"].map(AI_FRIENDLY).fillna("Other AI")
    ai["MONTH"] = pd.to_datetime(ai["MONTH"]).dt.tz_localize(None)

    print("Pulling storage metering...")
    st = qdf(conn, f"""
        SELECT usage_date::DATE AS day, SUM(bytes) / POW(1024, 4) AS storage_tb
        FROM {db}.STORAGE_SIMPLE_AVG_METERING
        WHERE account_id = {account_id}
          AND usage_date >= DATEADD('month', -{months_back}, CURRENT_DATE())
        GROUP BY 1 ORDER BY 1
    """)
    st["DAY"] = pd.to_datetime(st["DAY"]).dt.tz_localize(None)

    print("Pulling daily compute (last 90 days)...")
    daily = qdf(conn, f"""
        SELECT DATE_TRUNC('day', usage_time) AS day, SUM(credits) AS credits
        FROM {db}.WAREHOUSE_METERING
        WHERE account_id = {account_id}
          AND usage_time >= DATEADD('day', -90, CURRENT_DATE())
        GROUP BY 1 ORDER BY 1
    """)
    daily["DAY"] = pd.to_datetime(daily["DAY"]).dt.tz_localize(None)

    return wh, clust, tasks, gs, ai, st, daily


def pull_hourly_data(conn, account_id, deployment, days_back=90):
    """Pull hourly-granularity warehouse data for pattern analysis."""
    dep = deployment.upper()
    db  = f"METERING_BY_HOUR_{dep}.METERING"

    print("Pulling hourly warehouse data (90 days)...")
    df = qdf(conn, f"""
        SELECT entity_id,
               DATE_TRUNC('hour', usage_time) AS hour_ts,
               DAYOFWEEK(usage_time)           AS dow,
               HOUR(usage_time)                AS hour_of_day,
               DATE_TRUNC('day', usage_time)   AS day_ts,
               SUM(credits)                    AS credits
        FROM {db}.WAREHOUSE_METERING
        WHERE account_id = {account_id}
          AND usage_time >= DATEADD('day', -{days_back}, CURRENT_DATE())
        GROUP BY 1, 2, 3, 4, 5
    """)
    df["HOUR_TS"] = pd.to_datetime(df["HOUR_TS"]).dt.tz_localize(None)
    df["DAY_TS"]  = pd.to_datetime(df["DAY_TS"]).dt.tz_localize(None)
    return df


# ── WAREHOUSE PATTERN ANALYSIS ─────────────────────────────────────────────────
def _infer_size(credits_per_hr):
    """Estimate warehouse size from credits consumed per active hour."""
    if credits_per_hr <= 1.2:  return "XS"
    if credits_per_hr <= 2.5:  return "S"
    if credits_per_hr <= 5.0:  return "M"
    if credits_per_hr <= 10.0: return "L"
    if credits_per_hr <= 20.0: return "XL"
    if credits_per_hr <= 40.0: return "2XL"
    if credits_per_hr <= 80.0: return "3XL"
    return "4XL+"


def analyze_warehouses(wh_hourly, wh_monthly, wh_map):
    """
    Per-warehouse analysis of activity patterns, sizing, and multi-cluster fitness.
    Returns a dict keyed by entity_id with all metrics needed for recommendations.
    """
    results = {}
    for eid in wh_hourly["ENTITY_ID"].unique():
        df   = wh_hourly[wh_hourly["ENTITY_ID"] == eid].copy()
        name = wh_map.get(str(int(eid)), f"WH_{int(eid)}")

        total_90d = df["CREDITS"].sum()
        if total_90d < 1:
            continue

        active_hours   = len(df)
        active_days    = df["DAY_TS"].nunique()
        hrs_per_day    = active_hours / max(active_days, 1)

        # Weekend: Snowflake DAYOFWEEK returns 0=Sunday, 6=Saturday
        wend_credits   = df[df["DOW"].isin([0, 6])]["CREDITS"].sum()
        wend_pct       = wend_credits / total_90d * 100 if total_90d else 0

        # Overnight UTC 0–5 (roughly 8 pm–1 am ET)
        night_credits  = df[df["HOUR_OF_DAY"] < 6]["CREDITS"].sum()
        night_pct      = night_credits / total_90d * 100 if total_90d else 0

        # Business hours UTC 13–22 (~9 am–6 pm ET)
        biz_credits    = df[(df["HOUR_OF_DAY"] >= 13) & (df["HOUR_OF_DAY"] <= 22)]["CREDITS"].sum()
        biz_pct        = biz_credits / total_90d * 100 if total_90d else 0

        max_hr  = df["CREDITS"].max()
        avg_hr  = df["CREDITS"].mean()
        p90_hr  = df["CREDITS"].quantile(0.9)
        p50_hr  = df["CREDITS"].median()
        std_hr  = df["CREDITS"].std()
        cv      = std_hr / avg_hr if avg_hr > 0 else 0
        ratio   = max_hr / avg_hr  if avg_hr > 0 else 1

        peak_size    = _infer_size(max_hr)
        typical_size = _infer_size(p90_hr)
        min_size     = _infer_size(p50_hr)

        # Multi-cluster signal: bursty load pattern
        mc_signal = (cv > 1.5) or (ratio > 5) or (
            max_hr > 2 * p90_hr and total_90d > 50
        )

        # Hour-of-day concentration (high = scheduled batch job)
        hod_dist = df.groupby("HOUR_OF_DAY")["CREDITS"].sum()
        hod_conc = hod_dist.nlargest(3).sum() / hod_dist.sum() if hod_dist.sum() > 0 else 0

        dow_dist = df.groupby("DOW")["CREDITS"].sum()

        # 12-month trend: recent 3mo vs prior 3mo
        wh_12 = wh_monthly[wh_monthly["ENTITY_ID"] == eid].sort_values("MONTH")
        if len(wh_12) >= 3:
            r3  = wh_12.tail(3)["CREDITS"].mean()
            p3  = wh_12.iloc[-6:-3]["CREDITS"].mean() if len(wh_12) >= 6 else wh_12.head(3)["CREDITS"].mean()
            trend_pct = (r3 - p3) / p3 * 100 if p3 > 0 else 0
        else:
            trend_pct = 0

        results[eid] = dict(
            name=name, eid=eid,
            total_credits_90d=total_90d,
            credits_per_day_90d=total_90d / 90,
            active_hours_90d=active_hours,
            active_days_90d=active_days,
            active_hrs_per_day=hrs_per_day,
            weekend_pct=wend_pct,
            overnight_pct=night_pct,
            biz_pct=biz_pct,
            max_hour_credits=max_hr,
            avg_hour_credits=avg_hr,
            p50_hour_credits=p50_hr,
            p90_hour_credits=p90_hr,
            cv=cv,
            peak_avg_ratio=ratio,
            peak_size_estimate=peak_size,
            typical_size=typical_size,
            min_size=min_size,
            multi_cluster_signal=mc_signal,
            hod_concentration=hod_conc,
            trend_pct=trend_pct,
            hod_dist=hod_dist,
            dow_dist=dow_dist,
            df_hourly=df,
        )
    return results


# ── CHART HELPERS ─────────────────────────────────────────────────────────────
def fig_to_image(fig, width=6.8 * inch):
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    buf.seek(0)
    plt.close(fig)
    from PIL import Image as PILImage
    pil_img = PILImage.open(buf)
    pw, ph  = pil_img.size
    aspect  = ph / pw
    img     = Image(buf)
    img.drawWidth  = width
    img.drawHeight = width * aspect
    return img


def fmt_dollar(v):
    if v >= 1_000_000: return f"${v/1_000_000:.1f}M"
    if v >= 1_000:     return f"${v/1_000:.1f}K"
    return f"${v:,.0f}"


def chart_monthly_stacked(wh_mo, clust_mo, ai_mo, gs_mo, tk_mo, start_month):
    df = pd.concat([
        wh_mo.rename("Compute (Warehouses)"),
        clust_mo.rename("Auto-Clustering"),
        ai_mo.rename("AI / Cortex"),
        gs_mo.rename("Cloud Services"),
        tk_mo.rename("Scheduled Tasks"),
    ], axis=1).fillna(0).sort_index()
    df = df[df.index >= start_month]

    months = [d.strftime("%b %Y") for d in df.index]
    x = np.arange(len(months))
    bar_colors = [PALETTE[0], PALETTE[4], PALETTE[1], PALETTE[2], PALETTE[3]]

    fig, ax = plt.subplots(figsize=(10, 4.5))
    bottoms = np.zeros(len(df))
    for col, c in zip(df.columns, bar_colors):
        ax.bar(x, df[col], bottom=bottoms, color=c, label=col, width=0.65, edgecolor="white", linewidth=0.4)
        bottoms += df[col].values
    for i, total in enumerate(bottoms):
        ax.text(i, total + 15, f"{total:,.0f}", ha="center", va="bottom", fontsize=7.5, color="#333")
    ax.set_xticks(x)
    ax.set_xticklabels(months, fontsize=8.5, rotation=30, ha="right")
    ax.set_ylabel("Credits", fontsize=9)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:,.0f}"))
    ax.legend(fontsize=8, loc="upper left", framealpha=0.85)
    ax.set_title("Monthly Credit Consumption by Service Type", fontsize=11, fontweight="bold", pad=10)
    ax.spines["top"].set_visible(False); ax.spines["right"].set_visible(False)
    ax.set_axisbelow(True); ax.yaxis.grid(True, linestyle="--", alpha=0.4)
    fig.tight_layout()
    return fig_to_image(fig)


def chart_top_warehouse_bar(wa_results):
    """Horizontal bar chart of top warehouses by 90-day credits."""
    top = sorted(wa_results.values(), key=lambda x: x["total_credits_90d"], reverse=True)[:12]
    names = [w["name"] for w in top]
    credits = [w["total_credits_90d"] for w in top]
    bar_colors = [
        PALETTE[4] if w["total_credits_90d"] > 1000 else
        PALETTE[0] if w["total_credits_90d"] > 300 else
        PALETTE[2] for w in top
    ]

    fig, ax = plt.subplots(figsize=(10, max(3.5, len(top) * 0.4)))
    y = np.arange(len(names))
    bars = ax.barh(y, credits, color=bar_colors, edgecolor="white", linewidth=0.4)
    for bar, val in zip(bars, credits):
        ax.text(bar.get_width() + max(credits) * 0.01,
                bar.get_y() + bar.get_height() / 2,
                f"{val:,.0f} cr", va="center", fontsize=8)
    ax.set_yticks(y)
    ax.set_yticklabels(names, fontsize=8.5)
    ax.invert_yaxis()
    ax.set_xlabel("Credits (Last 90 Days)", fontsize=9)
    ax.set_title("Warehouse Credit Consumption — Last 90 Days", fontsize=11, fontweight="bold", pad=8)
    ax.spines["top"].set_visible(False); ax.spines["right"].set_visible(False)
    ax.set_axisbelow(True); ax.xaxis.grid(True, linestyle="--", alpha=0.4)
    fig.tight_layout()
    return fig_to_image(fig)


def chart_hourly_heatmap(wa_results):
    """Heatmap: top-10 warehouses (rows) × hour-of-day (cols), avg credits/day."""
    top = sorted(wa_results.values(), key=lambda x: x["total_credits_90d"], reverse=True)[:10]
    matrix = []
    row_labels = []
    for w in top:
        row = [w["hod_dist"].get(h, 0) / 90 for h in range(24)]
        matrix.append(row)
        row_labels.append(w["name"][:22])

    mat = np.array(matrix)
    fig, ax = plt.subplots(figsize=(11, max(3, len(top) * 0.45)))
    im = ax.imshow(mat, aspect="auto", cmap="YlOrRd", interpolation="nearest")
    ax.set_xticks(range(24))
    ax.set_xticklabels([f"{h:02d}:00" for h in range(24)], fontsize=7, rotation=45, ha="right")
    ax.set_yticks(range(len(row_labels)))
    ax.set_yticklabels(row_labels, fontsize=8)
    plt.colorbar(im, ax=ax, label="Avg Credits/Day (UTC)")
    ax.set_title("Warehouse Activity by Hour of Day (UTC) — Last 90 Days", fontsize=11, fontweight="bold", pad=8)
    fig.tight_layout()
    return fig_to_image(fig, width=7.0 * inch)


def chart_weekend_vs_weekday(wa_results):
    """Dual chart: weekday/weekend split + overnight % per warehouse."""
    top = sorted(wa_results.values(), key=lambda x: x["total_credits_90d"], reverse=True)[:10]
    names    = [w["name"][:20] for w in top]
    wday     = [100 - w["weekend_pct"] for w in top]
    wend     = [w["weekend_pct"] for w in top]
    overnight = [w["overnight_pct"] for w in top]
    x = np.arange(len(names))

    fig, axes = plt.subplots(1, 2, figsize=(11, max(3.5, len(top) * 0.42)))

    axes[0].barh(x, wday, color=PALETTE[0], label="Weekday", edgecolor="white", linewidth=0.3)
    axes[0].barh(x, wend, left=wday, color=PALETTE[3], label="Weekend", edgecolor="white", linewidth=0.3)
    axes[0].set_yticks(x); axes[0].set_yticklabels(names, fontsize=8.5); axes[0].invert_yaxis()
    axes[0].set_xlabel("% of Credits"); axes[0].legend(fontsize=8)
    axes[0].set_title("Weekday vs Weekend Activity", fontsize=10, fontweight="bold")
    axes[0].spines["top"].set_visible(False); axes[0].spines["right"].set_visible(False)
    axes[0].axvline(x=80, color="gray", linestyle="--", alpha=0.4)

    on_colors = [PALETTE[4] if v > 30 else PALETTE[3] if v > 10 else PALETTE[0] for v in overnight]
    axes[1].barh(x, overnight, color=on_colors, edgecolor="white", linewidth=0.3)
    for i, v in enumerate(overnight):
        if v > 3:
            axes[1].text(v + 0.5, i, f"{v:.0f}%", va="center", fontsize=8)
    axes[1].set_yticks(x); axes[1].set_yticklabels(names, fontsize=8.5); axes[1].invert_yaxis()
    axes[1].set_xlabel("% of Credits (UTC 00:00–05:59)")
    axes[1].set_title("Overnight Activity (UTC 0–6 = ~8pm–1am ET)", fontsize=10, fontweight="bold")
    axes[1].spines["top"].set_visible(False); axes[1].spines["right"].set_visible(False)

    fig.tight_layout()
    return fig_to_image(fig, width=7.0 * inch)


def chart_credit_distribution(wa_results):
    """Box plot of hourly credit distribution per warehouse (burstiness signal)."""
    top = sorted(wa_results.values(), key=lambda x: x["total_credits_90d"], reverse=True)[:10]
    names = [w["name"][:20] for w in top]
    data  = [w["df_hourly"]["CREDITS"].values for w in top]

    fig, ax = plt.subplots(figsize=(10, 4))
    ax.boxplot(
        data, vert=True, positions=list(range(len(top))), widths=0.5,
        patch_artist=True,
        boxprops=dict(facecolor="#dce8fa", color=PALETTE[0]),
        medianprops=dict(color=PALETTE[4], linewidth=2),
        whiskerprops=dict(color=PALETTE[0]),
        capprops=dict(color=PALETTE[0]),
        flierprops=dict(marker=".", color=PALETTE[4], markersize=3, alpha=0.4),
        showfliers=True,
    )
    ax.set_xticks(range(len(top)))
    ax.set_xticklabels(names, rotation=35, ha="right", fontsize=8.5)
    ax.set_ylabel("Credits per Hour", fontsize=9)
    ax.set_title(
        "Credit Distribution per Hour  (Wide Spread = Bursty = Multi-Cluster Candidate)",
        fontsize=11, fontweight="bold", pad=8,
    )
    ax.spines["top"].set_visible(False); ax.spines["right"].set_visible(False)
    ax.set_axisbelow(True); ax.yaxis.grid(True, linestyle="--", alpha=0.4)
    fig.tight_layout()
    return fig_to_image(fig)


def chart_clustering_monthly(clust_mo, start_month):
    cm = clust_mo[clust_mo.index >= start_month]
    months = [d.strftime("%b %Y") for d in cm.index]
    x = np.arange(len(months))
    bar_colors = [PALETTE[4] if v > 500 else PALETTE[0] for v in cm.values]

    fig, ax = plt.subplots(figsize=(10, 3.5))
    bars = ax.bar(x, cm.values, color=bar_colors, width=0.65, edgecolor="white", linewidth=0.4)
    for bar, val in zip(bars, cm.values):
        if val > 5:
            ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 10,
                    f"{val:,.0f}", ha="center", va="bottom", fontsize=8)
    ax.set_xticks(x); ax.set_xticklabels(months, fontsize=8.5)
    ax.set_ylabel("Credits", fontsize=9)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:,.0f}"))
    ax.set_title("Automatic Clustering Credits by Month", fontsize=11, fontweight="bold", pad=10)
    ax.spines["top"].set_visible(False); ax.spines["right"].set_visible(False)
    ax.set_axisbelow(True); ax.yaxis.grid(True, linestyle="--", alpha=0.4)
    fig.tight_layout()
    return fig_to_image(fig)


def chart_clustering_entities(clust, credit_rate):
    clust_recent = clust[clust["MONTH"] >= clust["MONTH"].max() - pd.DateOffset(months=3)]
    by_entity = clust_recent.groupby("ENTITY_ID")["CREDITS"].sum().sort_values(ascending=False).head(10)
    labels = [f"Table ...{str(eid)[-8:]}" for eid in by_entity.index]

    fig, ax = plt.subplots(figsize=(9, 4))
    y = np.arange(len(labels))
    bar_colors = [PALETTE[4] if v > 500 else PALETTE[0] for v in by_entity.values]
    bars = ax.barh(y, by_entity.values, color=bar_colors, edgecolor="white", linewidth=0.4)
    for bar, val in zip(bars, by_entity.values):
        ax.text(bar.get_width() + 5, bar.get_y() + bar.get_height() / 2,
                f"{val:,.0f} cr (${val*credit_rate:,.0f})", va="center", fontsize=8)
    ax.set_yticks(y); ax.set_yticklabels(labels, fontsize=8.5); ax.invert_yaxis()
    ax.set_xlabel("Credits", fontsize=9)
    ax.set_title("Top Clustering Table Entities (Last 3 Months)", fontsize=11, fontweight="bold", pad=10)
    ax.spines["top"].set_visible(False); ax.spines["right"].set_visible(False)
    ax.set_axisbelow(True); ax.xaxis.grid(True, linestyle="--", alpha=0.4)
    fig.tight_layout()
    return fig_to_image(fig, width=6.5 * inch)


def chart_ai_stacked(ai, start_month):
    pivot = ai.groupby(["MONTH", "service"])["CREDITS"].sum().unstack(fill_value=0).sort_index()
    pivot = pivot[pivot.index >= start_month]
    top_svcs = pivot.sum().nlargest(7).index.tolist()
    pivot = pivot[top_svcs] if top_svcs else pivot

    months = [d.strftime("%b %Y") for d in pivot.index]
    x = np.arange(len(months))
    fig, ax = plt.subplots(figsize=(10, 3.5))
    bottoms = np.zeros(len(pivot))
    for i, col in enumerate(pivot.columns):
        ax.bar(x, pivot[col], bottom=bottoms, color=PALETTE[i], label=col, width=0.65, edgecolor="white", linewidth=0.4)
        bottoms += pivot[col].values
    ax.set_xticks(x); ax.set_xticklabels(months, fontsize=8.5)
    ax.set_ylabel("Credits", fontsize=9)
    ax.legend(fontsize=7.5, loc="upper left", framealpha=0.85, ncol=2)
    ax.set_title("AI / Cortex Monthly Credits by Product", fontsize=11, fontweight="bold", pad=10)
    ax.spines["top"].set_visible(False); ax.spines["right"].set_visible(False)
    ax.set_axisbelow(True); ax.yaxis.grid(True, linestyle="--", alpha=0.4)
    fig.tight_layout()
    return fig_to_image(fig)


def chart_daily(daily):
    df = daily.sort_values("DAY").copy()
    df["rolling"] = df["CREDITS"].rolling(7, min_periods=1).mean()
    fig, ax = plt.subplots(figsize=(10, 3))
    ax.fill_between(df["DAY"], df["CREDITS"], alpha=0.18, color=PALETTE[0])
    ax.plot(df["DAY"], df["CREDITS"], color=PALETTE[0], linewidth=1.5, label="Daily")
    ax.plot(df["DAY"], df["rolling"], color=PALETTE[4], linewidth=2, linestyle="--", label="7-day avg")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
    ax.xaxis.set_major_locator(mdates.WeekdayLocator(byweekday=0, interval=2))
    plt.setp(ax.xaxis.get_majorticklabels(), rotation=30, ha="right", fontsize=8)
    ax.set_ylabel("Credits/day", fontsize=9)
    ax.legend(fontsize=8)
    ax.set_title("Daily Warehouse Compute — Last 90 Days", fontsize=11, fontweight="bold", pad=10)
    ax.spines["top"].set_visible(False); ax.spines["right"].set_visible(False)
    ax.set_axisbelow(True); ax.yaxis.grid(True, linestyle="--", alpha=0.4)
    fig.tight_layout()
    return fig_to_image(fig)


def chart_storage(st, start_month):
    df = st.sort_values("DAY").copy()
    df["month"] = df["DAY"].dt.to_period("M")
    mo_avg = df.groupby("month")["STORAGE_TB"].mean().reset_index()
    mo_avg["month_dt"] = mo_avg["month"].dt.to_timestamp()
    mo_avg = mo_avg[mo_avg["month_dt"] >= start_month]

    fig, ax = plt.subplots(figsize=(10, 2.8))
    ax.bar(mo_avg["month_dt"], mo_avg["STORAGE_TB"], width=20, color=PALETTE[2], edgecolor="white", alpha=0.85)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
    ax.xaxis.set_major_locator(mdates.MonthLocator())
    plt.setp(ax.xaxis.get_majorticklabels(), rotation=30, ha="right", fontsize=8)
    ax.set_ylabel("TB (avg)", fontsize=9)
    ax.set_title("Average Monthly Storage (TB)", fontsize=11, fontweight="bold", pad=10)
    ax.spines["top"].set_visible(False); ax.spines["right"].set_visible(False)
    ax.set_axisbelow(True); ax.yaxis.grid(True, linestyle="--", alpha=0.4)
    for bar in ax.patches:
        h = bar.get_height()
        if h > 0.5:
            ax.text(bar.get_x() + bar.get_width() / 2, h, f"{h:.1f}",
                    ha="center", va="bottom", fontsize=7)
    fig.tight_layout()
    return fig_to_image(fig)


# ── PDF STYLES ─────────────────────────────────────────────────────────────────
def make_styles():
    s = getSampleStyleSheet()
    s.add(ParagraphStyle("HCCover1", fontName="Helvetica-Bold",  fontSize=26, leading=32, textColor=colors.white, alignment=TA_LEFT))
    s.add(ParagraphStyle("HCCover2", fontName="Helvetica",       fontSize=13, leading=18, textColor=colors.Color(0.8, 0.9, 1.0), alignment=TA_LEFT))
    s.add(ParagraphStyle("HCCover3", fontName="Helvetica",       fontSize=9.5, leading=14, textColor=colors.Color(0.7, 0.8, 0.9), alignment=TA_LEFT))
    s.add(ParagraphStyle("HCSH",     fontName="Helvetica-Bold",  fontSize=14, leading=20, textColor=SF_BLUE, spaceBefore=14, spaceAfter=4))
    s.add(ParagraphStyle("HCSubH",   fontName="Helvetica-Bold",  fontSize=10, leading=14, textColor=SF_DARK, spaceBefore=8, spaceAfter=2))
    s.add(ParagraphStyle("HCBody",   fontName="Helvetica",       fontSize=9,  leading=13, textColor=SF_DARK, spaceAfter=5))
    s.add(ParagraphStyle("HCSmall",  fontName="Helvetica",       fontSize=7.5, leading=11, textColor=colors.Color(0.4, 0.4, 0.4)))
    s.add(ParagraphStyle("HCBullet", fontName="Helvetica",       fontSize=9,  leading=13, textColor=SF_DARK, spaceAfter=4, leftIndent=12, firstLineIndent=-8))
    s.add(ParagraphStyle("HCCode",   fontName="Courier",         fontSize=7.5, leading=11, textColor=SF_DARK, backColor=colors.Color(0.95, 0.95, 0.98), leftIndent=10, rightIndent=10, spaceAfter=2))
    s.add(ParagraphStyle("HCRecBox", fontName="Helvetica-Bold",  fontSize=9.5, leading=13, textColor=SF_BLUE, spaceBefore=8))
    s.add(ParagraphStyle("HCTblCell",fontName="Helvetica",       fontSize=8,   leading=11, textColor=SF_DARK))
    s.add(ParagraphStyle("HCTblHdr", fontName="Helvetica-Bold",  fontSize=8,   leading=11, textColor=colors.white))
    return s


def _tbl(rows, col_w):
    """Simple header table with alternating row colors."""
    t = Table(rows, colWidths=col_w)
    t.setStyle(TableStyle([
        ("FONTNAME",       (0, 0), (-1, 0),  "Helvetica-Bold"),
        ("FONTSIZE",       (0, 0), (-1, -1), 8),
        ("BACKGROUND",     (0, 0), (-1, 0),  SF_BLUE),
        ("TEXTCOLOR",      (0, 0), (-1, 0),  colors.white),
        ("ALIGN",          (1, 0), (-1, -1), "RIGHT"),
        ("ALIGN",          (0, 0), (0, -1),  "LEFT"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.Color(0.96, 0.97, 1.0)]),
        ("GRID",           (0, 0), (-1, -1), 0.3, colors.Color(0.85, 0.85, 0.85)),
        ("VALIGN",         (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",     (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING",  (0, 0), (-1, -1), 4),
        ("LEFTPADDING",    (0, 0), (-1, -1), 6),
    ]))
    return t


def _para_tbl(rows, col_w):
    """Table where cells contain Paragraph objects (supports text wrapping)."""
    t = Table(rows, colWidths=col_w)
    t.setStyle(TableStyle([
        ("BACKGROUND",     (0, 0), (-1, 0),  SF_BLUE),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.Color(0.96, 0.97, 1.0)]),
        ("GRID",           (0, 0), (-1, -1), 0.3, colors.Color(0.85, 0.85, 0.85)),
        ("VALIGN",         (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING",     (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING",  (0, 0), (-1, -1), 5),
        ("LEFTPADDING",    (0, 0), (-1, -1), 5),
    ]))
    return t


def kpi_table(kpis, styles):
    cells = []
    for label, value, sub in kpis:
        block = [
            Paragraph(f'<font size=16 color="#0757B2"><b>{value}</b></font>', styles["HCBody"]),
            Paragraph(f'<font size=8 color="#555">{label}</font>', styles["HCBody"]),
        ]
        if sub:
            block.append(Paragraph(f'<font size=7 color="#888">{sub}</font>', styles["HCSmall"]))
        cells.append(block)
    t = Table([cells], colWidths=[1.68 * inch] * len(kpis))
    t.setStyle(TableStyle([
        ("BOX",          (0, 0), (-1, -1), 0.5, colors.Color(0.85, 0.90, 0.98)),
        ("INNERGRID",    (0, 0), (-1, -1), 0.5, colors.Color(0.85, 0.90, 0.98)),
        ("BACKGROUND",   (0, 0), (-1, -1), colors.Color(0.96, 0.97, 1.0)),
        ("VALIGN",       (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING",   (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 8),
        ("LEFTPADDING",  (0, 0), (-1, -1), 10),
    ]))
    return t


def _rec_block(story, title, bullets, styles, badge=None):
    badge_txt = f"  <font size=8 color='#888'>[{badge}]</font>" if badge else ""
    story.append(Paragraph(f"<b>{title}</b>{badge_txt}", styles["HCRecBox"]))
    for b in bullets:
        story.append(Paragraph(f"&#x2022; {b}", styles["HCBullet"]))


# ── PER-WAREHOUSE RECOMMENDATION ENGINE ───────────────────────────────────────
def gen_warehouse_recs(wa):
    """
    Return list of (title, [bullet lines], badge) for a warehouse.
    Logic is entirely metric-driven — no hardcoded warehouse names.
    """
    recs = []
    name      = wa["name"]
    total     = wa["total_credits_90d"]
    cv        = wa["cv"]
    overnight = wa["overnight_pct"]
    weekend   = wa["weekend_pct"]
    peak_size = wa["peak_size_estimate"]
    typical   = wa["typical_size"]
    min_size  = wa["min_size"]
    ratio     = wa["peak_avg_ratio"]
    avg_hr    = wa["avg_hour_credits"]

    is_etl    = any(t in name.upper() for t in ["ETL", "DBT", "DATABRICKS", "SERVICE"])
    is_dev    = any(t in name.upper() for t in ["DEVELOPER", "DEV_"])
    is_bi     = any(t in name.upper() for t in ["BI", "TABLEAU", "LOOKER", "SIGMA", "POWER"])

    # Sizing mismatch
    if peak_size != typical and peak_size != min_size:
        recs.append((
            f"{name}: Right-Sizing Opportunity",
            [
                f"Peak usage suggests a {peak_size} warehouse, but typical (p90) load runs at {typical} and median at {min_size}.",
                f"Configured at {peak_size} to handle rare spikes — most hours it is underutilized.",
                f"Recommended: Size down to {typical} as the base size and enable Multi-Cluster (min=1, max=2–3) to handle "
                f"peak bursts rather than keeping a large single-cluster warehouse running full-time.",
                f"This prevents the 'big warehouse idle' pattern where a {peak_size} burns full credits even at low utilization.",
            ],
            "Sizing"
        ))

    # Multi-cluster signal
    if wa["multi_cluster_signal"] and total > 50:
        recs.append((
            f"{name}: Enable Multi-Cluster Warehousing",
            [
                f"Coefficient of variation: {cv:.1f} — ratio of peak to average: {ratio:.1f}x.",
                f"This pattern indicates concurrent query queuing — multiple queries arriving in short windows.",
                f"Recommended: Enable multi-cluster with MIN_CLUSTER_COUNT=1, MAX_CLUSTER_COUNT=2 or 3.",
                f"Set SCALING_POLICY=ECONOMY to avoid spinning up extra clusters for light bursts.",
            ],
            "Multi-Cluster"
        ))

    # Unexpected overnight activity (skip for known ETL/batch warehouses)
    if overnight > 20 and not is_etl:
        recs.append((
            f"{name}: Unexpected Overnight Activity ({overnight:.0f}% of credits at UTC 0–6)",
            [
                f"For this warehouse, {overnight:.0f}% of credits running overnight (UTC 0–6 ≈ 8 pm–1 am ET) is worth investigating.",
                f"Check if scheduled reports, BI dashboard refreshes, or query tools are keeping this warehouse warm.",
                f"Ensure AUTO_SUSPEND is set to 60 seconds to prevent idle warehouse costs overnight.",
                f"If this is intentional batch work, consider consolidating into a dedicated ETL warehouse with a tighter schedule.",
            ],
            "Auto-Suspend"
        ))
    elif not is_etl and total > 100:
        recs.append((
            f"{name}: Verify AUTO_SUSPEND Setting",
            [
                f"Confirm AUTO_SUSPEND is set to 60 seconds (many warehouses default to 600 s / 10 min).",
                f"At {avg_hr:.1f} credits/hr average, each idle 10-minute window wastes ~{avg_hr/6:.1f} credits.",
                f"Run: ALTER WAREHOUSE {name} SET AUTO_SUSPEND = 60;",
            ],
            "Auto-Suspend"
        ))

    # Unexpected weekend activity for non-ETL warehouses
    if weekend > 30 and not is_etl:
        recs.append((
            f"{name}: High Weekend Activity ({weekend:.0f}% of credits)",
            [
                f"Non-ETL warehouse spending {weekend:.0f}% of credits on weekends.",
                f"Possible causes: BI tool dashboard refreshes, scheduled reports, or developers running ad-hoc queries.",
                f"Review weekend workloads — if BI tools are auto-refreshing dashboards, reschedule to off-peak weekday hours.",
            ],
            "Scheduling"
        ))

    return recs


# ── PRIORITY ACTION MATRIX (DATA-DRIVEN) ──────────────────────────────────────
def build_priority_matrix(wa_results, styles):
    """
    Generate a fully data-driven priority action matrix based on top-5
    warehouses by 90-day spend. No hardcoded warehouse names.
    """
    top5 = sorted(wa_results.values(), key=lambda x: x["total_credits_90d"], reverse=True)[:5]

    hs = styles["HCTblHdr"]
    cs = styles["HCTblCell"]

    def prow(num, wh_name, action_text, outcome_text):
        return [
            Paragraph(str(num), cs),
            Paragraph(wh_name, cs),
            Paragraph(action_text, cs),
            Paragraph(outcome_text, cs),
        ]

    rows = [[
        Paragraph("#",                hs),
        Paragraph("Warehouse",        hs),
        Paragraph("Action",           hs),
        Paragraph("Expected Outcome", hs),
    ]]

    n = 1
    for wa in top5:
        name      = wa["name"]
        total     = wa["total_credits_90d"]
        is_etl    = any(t in name.upper() for t in ["ETL", "DBT", "DATABRICKS", "SERVICE"])
        is_dev    = any(t in name.upper() for t in ["DEVELOPER", "DEV_"])
        overnight = wa["overnight_pct"]
        mc        = wa["multi_cluster_signal"]
        cv        = wa["cv"]
        ratio     = wa["peak_avg_ratio"]
        peak_size = wa["peak_size_estimate"]
        typical   = wa["typical_size"]
        min_size  = wa["min_size"]

        if is_dev:
            action = (
                f"Set AUTO_SUSPEND = 60 on this developer warehouse (currently burning {total:,.0f} credits/90 days). "
                f"Restrict size to S or M — developer ad-hoc queries rarely justify L+. "
                f"Review whether all current users require access to this warehouse."
            )
            outcome = "Eliminate idle developer warehouse cost"
        elif mc and peak_size != typical:
            # Multi-cluster + sizing mismatch — valid for all warehouse types including ETL/DBT
            etl_note = " Review dbt model concurrency settings and enable incremental models to reduce full-refresh runs." if "DBT" in name.upper() else ""
            action = (
                f"Enable multi-cluster (MIN=1, MAX=2–3, SCALING_POLICY=ECONOMY) — "
                f"CV={cv:.1f}, peak/avg ratio={ratio:.1f}x signals concurrent query queuing. "
                f"Size down base from {peak_size} to {typical}; most hours run well below peak."
                f"{etl_note}"
            )
            outcome = "Reduce query queuing and right-size warehouse base"
        elif mc:
            etl_note = " Use Script #2 to find repetitive dbt model queries or validation steps that could be batched." if "DBT" in name.upper() else ""
            action = (
                f"Enable multi-cluster (MIN=1, MAX=2–3, SCALING_POLICY=ECONOMY). "
                f"CV={cv:.1f}, peak/avg ratio={ratio:.1f}x indicates concurrent queries queuing without additional clusters."
                f"{etl_note}"
            )
            outcome = "Eliminate concurrent query queuing"
        elif peak_size != typical:
            action = (
                f"Warehouse peaks at {peak_size} but typical (p90) usage is {typical} and median is {min_size}. "
                f"Size down to {typical} and enable multi-cluster (min=1, max=2) to handle burst traffic "
                f"without permanently running at the larger size."
            )
            outcome = "Right-size warehouse; eliminate routine idle overhead"
        elif overnight > 20 and not is_etl:
            action = (
                f"{overnight:.0f}% of credits consumed overnight (UTC 0–6). "
                f"Investigate scheduled jobs, BI tool refreshes, or query tools keeping this warehouse warm. "
                f"Set AUTO_SUSPEND = 60 and audit overnight workloads using Script #4 (Hourly Utilization)."
            )
            outcome = "Eliminate unexpected overnight credit burn"
        else:
            etl_note = " Investigate whether ETL jobs can run less frequently or be batched." if is_etl else ""
            action = (
                f"Run Script #1 (Top Queries) and Script #2 (Repetitive Queries) for this warehouse "
                f"to identify the specific queries driving credit consumption.{etl_note}"
            )
            outcome = "Pinpoint specific queries driving cost"
        rows.append(prow(n, name, action, outcome))
        n += 1

    # Always-present generic rows
    rows.append(prow(n, "All Warehouses",
        "Audit AUTO_SUSPEND settings. Run SHOW WAREHOUSES; and confirm AUTO_SUSPEND <= 60 for all interactive "
        "and BI warehouses. A 10-minute default wastes significant credits between queries.",
        "Reduce idle overhead across entire account"))
    n += 1

    rows.append(prow(n, "All Warehouses",
        "Run Script #2 (Repetitive Queries) to surface queries executing 20+ times/day with the same query hash. "
        "Candidates: BI dashboard auto-refreshes, scheduled report queries, repeated ETL validation steps. "
        "Evaluate result caching, pre-aggregated tables, or Dynamic Tables for materialization.",
        "Eliminate redundant compute for repeated queries"))

    return rows


# ── BUILD PDF ──────────────────────────────────────────────────────────────────
def build_pdf(wh, wh_hourly, clust, tasks, gs, ai, st, daily, wa_results, config):
    credit_rate = config["credit_rate"]
    company     = config["company_name"]
    locator     = config["locator"]
    deployment  = config["deployment"]
    out_path    = config["output"]
    wh_map      = config["warehouse_map"]
    account_id  = config["account_id"]

    styles      = make_styles()
    w, h        = letter
    content_w   = w - 1.3 * inch
    report_date = datetime.now().strftime("%B %d, %Y")

    # Map entity IDs → warehouse names in monthly data
    wh["warehouse"] = wh["ENTITY_ID"].map(lambda e: wh_map.get(str(int(e)), f"WH_{int(e)}"))

    # Monthly series
    wh_mo    = wh.groupby("MONTH")["CREDITS"].sum()
    clust_mo = clust.groupby("MONTH")["CREDITS"].sum() if not clust.empty else pd.Series(dtype=float)
    ai_mo    = ai.groupby("MONTH")["CREDITS"].sum()    if not ai.empty  else pd.Series(dtype=float)
    gs_mo    = gs.set_index("MONTH")["CREDITS"]        if not gs.empty  else pd.Series(dtype=float)
    tk_mo    = tasks.set_index("MONTH")["CREDITS"]     if not tasks.empty else pd.Series(dtype=float)

    all_months  = sorted(set(wh_mo.index) | set(clust_mo.index) | set(ai_mo.index) | set(gs_mo.index))
    start_month = min(all_months) if all_months else pd.Timestamp("2025-01-01")

    def total_mo(mo):
        return (wh_mo.get(mo, 0) + clust_mo.get(mo, 0) + ai_mo.get(mo, 0)
                + gs_mo.get(mo, 0) + tk_mo.get(mo, 0))

    last_full  = sorted(all_months)[-2] if len(all_months) >= 2 else all_months[-1]
    prev_full  = sorted(all_months)[-3] if len(all_months) >= 3 else last_full
    last_total = total_mo(last_full)
    prev_total = total_mo(prev_full)
    mom_pct    = (last_total - prev_total) / prev_total * 100 if prev_total else 0
    annualized = last_total * 12

    ytd_start   = pd.Timestamp(f"{datetime.now().year}-01-01")
    ytd_credits = sum(total_mo(mo) for mo in all_months if mo >= ytd_start)

    has_clustering = not clust.empty and clust["CREDITS"].sum() > 10
    has_ai         = not ai.empty   and ai["CREDITS"].sum()   > 1
    clust_total    = clust["CREDITS"].sum() if has_clustering else 0

    # Sorted warehouses by 90-day spend
    wh_90 = dict(sorted(wa_results.items(), key=lambda x: x[1]["total_credits_90d"], reverse=True))

    # ── GENERATE CHARTS ──
    print("Generating charts...")
    img_monthly   = chart_monthly_stacked(wh_mo, clust_mo, ai_mo, gs_mo, tk_mo, start_month)
    img_wh_bar    = chart_top_warehouse_bar(wa_results)
    img_heatmap   = chart_hourly_heatmap(wh_90)
    img_wknd      = chart_weekend_vs_weekday(wh_90)
    img_distrib   = chart_credit_distribution(wh_90)
    img_daily     = chart_daily(daily)      if not daily.empty else None
    img_storage   = chart_storage(st, start_month) if not st.empty else None
    img_clust_bar = chart_clustering_monthly(clust_mo, start_month) if has_clustering else None
    img_clust_ent = chart_clustering_entities(clust, credit_rate)   if has_clustering else None
    img_ai_stack  = chart_ai_stacked(ai, start_month)               if has_ai         else None

    # ── DOC SETUP ──
    doc = SimpleDocTemplate(
        out_path, pagesize=letter,
        topMargin=0.65 * inch, bottomMargin=0.45 * inch,
        leftMargin=0.65 * inch, rightMargin=0.65 * inch,
    )

    def on_page(canvas, doc_obj):
        canvas.saveState()
        if doc_obj.page > 1:
            canvas.setFillColor(SF_BLUE)
            canvas.rect(0, h - 0.45 * inch, w, 0.45 * inch, fill=1, stroke=0)
            canvas.setFillColor(colors.white)
            canvas.setFont("Helvetica-Bold", 9)
            canvas.drawString(0.5 * inch, h - 0.28 * inch, f"{company} — Snowflake Usage Health Check")
            canvas.setFont("Helvetica", 9)
            canvas.drawRightString(w - 0.5 * inch, h - 0.28 * inch,
                                   f"{report_date}  |  Confidential  |  Page {doc_obj.page}")
            canvas.setFillColor(colors.Color(0.6, 0.6, 0.6))
            canvas.setFont("Helvetica", 7)
            canvas.drawCentredString(w / 2, 0.25 * inch, "Confidential — Prepared by Snowflake Account Team")
        canvas.restoreState()

    story = []

    # ══ PAGE 1: COVER ══════════════════════════════════════════════════════════
    cover_t = Table([[""]], colWidths=[content_w], rowHeights=[2.4 * inch])
    cover_t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), SF_BLUE)]))
    story.append(Spacer(1, 0.3 * inch))
    story.append(cover_t)
    story.append(Spacer(1, 0.12 * inch))
    story.append(Paragraph(company, styles["HCCover1"]))
    story.append(Paragraph("Snowflake Usage &amp; Cost Health Check", styles["HCCover2"]))
    story.append(Spacer(1, 0.12 * inch))
    story.append(Paragraph(
        f"Prepared: {report_date}  |  Account: {locator}  |  Region: {deployment.upper()}",
        styles["HCCover3"]
    ))
    story.append(Paragraph(
        f"Credit Rate: ${credit_rate}/credit  |  Analysis: "
        f"{start_month.strftime('%b %Y')} – {sorted(all_months)[-1].strftime('%b %Y')} (monthly) + Last 90 days (hourly)",
        styles["HCCover3"]
    ))
    story.append(Spacer(1, 0.15 * inch))
    accent = Table([[""]], colWidths=[content_w], rowHeights=[0.08 * inch])
    accent.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), SF_CYAN)]))
    story.append(accent)
    story.append(Spacer(1, 0.2 * inch))

    story.append(Paragraph("Key Highlights", ParagraphStyle(
        "HCHLHdr", fontName="Helvetica-Bold", fontSize=11, leading=15, textColor=SF_BLUE)))
    highlights = [
        f"<b>{int(ytd_credits):,} total credits</b> YTD ({datetime.now().year}) — est. ${ytd_credits*credit_rate:,.0f}",
        f"<b>Annualized run rate: {fmt_dollar(annualized * credit_rate)}</b> based on {last_full.strftime('%b %Y')}",
    ]
    if wh_90:
        top_wh = list(wh_90.values())[0]
        highlights.append(
            f"<b>{len(wh_90)} active warehouses</b> — top spender: {top_wh['name']} "
            f"({top_wh['total_credits_90d']:,.0f} credits last 90 days)"
        )
    if has_clustering:
        highlights.append(
            f"<b>Auto-clustering: {int(clust_total):,} credits (${clust_total*credit_rate:,.0f})</b> — optimization opportunity"
        )
    if has_ai:
        ai_total = ai["CREDITS"].sum()
        highlights.append(
            f"<b>AI/Cortex active</b>: {ai_total:,.1f} credits across {ai['service'].nunique()} products"
        )
    hl_style = ParagraphStyle("HCHLItem", fontName="Helvetica", fontSize=9.5, leading=15,
                               textColor=SF_DARK, spaceAfter=5, leftIndent=8)
    for hl in highlights:
        story.append(Paragraph(f"&#x2022; &nbsp; {hl}", hl_style))

    story.append(PageBreak())

    # ══ PAGE 2: EXECUTIVE SUMMARY ══════════════════════════════════════════════
    story.append(Paragraph("Executive Summary", styles["HCSH"]))
    story.append(HRFlowable(width=content_w, thickness=1.5, color=SF_BLUE, spaceAfter=10))

    kpis = [
        (f"YTD Credits ({datetime.now().year})", f"{int(ytd_credits):,}", f"${ytd_credits*credit_rate:,.0f}"),
        (f"{last_full.strftime('%b %Y')} Credits", f"{int(last_total):,}", f"${last_total*credit_rate:,.0f}"),
        ("MoM Change", f"{mom_pct:+.0f}%", f"{prev_full.strftime('%b')}→{last_full.strftime('%b')}"),
        ("Ann. Run Rate", f"{int(annualized):,} cr", f"${annualized*credit_rate:,.0f}/yr"),
        ("Active Warehouses", str(len([v for v in wa_results.values() if v["total_credits_90d"] > 5])), "Last 90 days"),
    ]
    if has_clustering:
        kpis.append(("Clustering Spend", f"{int(clust_total):,} cr", f"${clust_total*credit_rate:,.0f}"))
    story.append(kpi_table(kpis, styles))
    story.append(Spacer(1, 0.12 * inch))
    story.append(img_monthly)

    monthly_rows = [["Month", "Compute", "Clustering", "AI/Cortex", "Cloud Svcs", "Tasks", "Total", "Est. Cost"]]
    for mo in sorted(all_months, reverse=True):
        wv = wh_mo.get(mo, 0); cv2 = clust_mo.get(mo, 0); av = ai_mo.get(mo, 0)
        gv = gs_mo.get(mo, 0);  tv = tk_mo.get(mo, 0);   tot = wv + cv2 + av + gv + tv
        monthly_rows.append([
            mo.strftime("%b %Y"), f"{wv:,.0f}", f"{cv2:,.0f}", f"{av:,.0f}",
            f"{gv:,.0f}", f"{tv:,.0f}", f"{tot:,.0f}", f"${tot*credit_rate:,.0f}"
        ])
    story.append(Paragraph("Monthly Credit Summary", styles["HCSubH"]))
    story.append(_tbl(monthly_rows, [0.82 * inch] * 8))
    story.append(PageBreak())

    # ══ PAGE 3: WAREHOUSE CREDIT BREAKDOWN ════════════════════════════════════
    story.append(Paragraph("Warehouse Credit Breakdown — Last 90 Days", styles["HCSH"]))
    story.append(HRFlowable(width=content_w, thickness=1.5, color=SF_BLUE, spaceAfter=6))
    story.append(img_wh_bar)
    story.append(Spacer(1, 0.08 * inch))

    wh_tbl_rows = [["Warehouse", "Credits (90d)", "Avg Cr/Day", "Max Hr", "Avg Active Hrs/Day", "90-Day Trend"]]
    for wa in list(wh_90.values())[:15]:
        trend_arrow = "▲" if wa["trend_pct"] > 5 else "▼" if wa["trend_pct"] < -5 else "—"
        wh_tbl_rows.append([
            wa["name"],
            f"{wa['total_credits_90d']:,.0f}",
            f"{wa['credits_per_day_90d']:.1f}",
            f"{wa['max_hour_credits']:.1f}",
            f"{wa['active_hrs_per_day']:.1f}",
            f"{trend_arrow} {abs(wa['trend_pct']):.0f}%",
        ])
    story.append(_tbl(wh_tbl_rows, [2.0 * inch, 0.9 * inch, 0.85 * inch, 0.7 * inch, 1.1 * inch, 0.8 * inch]))
    story.append(Spacer(1, 0.08 * inch))
    story.append(Paragraph(
        "Trend = most recent 3 months vs prior 3 months. Max Hr = highest credits consumed in any single hour.",
        styles["HCSmall"]
    ))
    story.append(PageBreak())

    # ══ PAGE 4: HOURLY PATTERNS ════════════════════════════════════════════════
    story.append(Paragraph("Hourly Activity Patterns &amp; Scheduling Signals", styles["HCSH"]))
    story.append(HRFlowable(width=content_w, thickness=1.5, color=SF_BLUE, spaceAfter=6))
    story.append(Paragraph(
        "The heatmap shows when each warehouse actively consumes credits by hour of day (UTC). "
        "Dark bands indicate scheduled job windows; uneven patterns suggest concurrency issues. "
        "<b>UTC times — US Eastern is UTC-4 (summer) / UTC-5 (winter).</b>",
        styles["HCBody"]
    ))
    story.append(img_heatmap)
    story.append(Spacer(1, 0.1 * inch))

    story.append(Paragraph("Key Scheduling Observations", styles["HCSubH"]))
    for wa in list(wh_90.values())[:8]:
        hod_conc = wa["hod_concentration"]
        top_hrs  = wa["hod_dist"].nlargest(3)
        hr_str   = ", ".join([f"{h:02d}:00 UTC" for h in top_hrs.index])
        if hod_conc > 0.7:
            obs = (f"<b>{wa['name']}</b>: {hod_conc*100:.0f}% of credits concentrated in top-3 hours "
                   f"({hr_str}). Clear scheduled batch job pattern.")
        elif hod_conc > 0.45:
            obs = (f"<b>{wa['name']}</b>: Moderate scheduling pattern ({hod_conc*100:.0f}% in top-3 hours) — "
                   f"mix of scheduled and interactive queries.")
        else:
            obs = (f"<b>{wa['name']}</b>: Activity distributed across the day — "
                   f"primarily interactive / ad-hoc workload.")
        story.append(Paragraph(f"&#x2022; {obs}", styles["HCBullet"]))
    story.append(PageBreak())

    # ══ PAGE 5: WEEKEND & OVERNIGHT AUDIT ═════════════════════════════════════
    story.append(Paragraph("Weekend &amp; Overnight Activity Audit", styles["HCSH"]))
    story.append(HRFlowable(width=content_w, thickness=1.5, color=SF_BLUE, spaceAfter=6))
    story.append(Paragraph(
        "Unexpected weekend or overnight activity is often a direct savings lever — idle warehouses kept warm by BI tools, "
        "over-broad ETL schedules, or AUTO_SUSPEND set too high.",
        styles["HCBody"]
    ))
    story.append(img_wknd)
    story.append(Spacer(1, 0.08 * inch))

    wknd_rows = [["Warehouse", "90d Credits", "Weekend %", "Overnight % (UTC 0–6)", "Likely Automated?", "Action"]]
    for wa in list(wh_90.values())[:12]:
        is_auto = ("Yes"    if wa["hod_concentration"] > 0.5 or
                              any(t in wa["name"].upper() for t in ["ETL", "DBT", "SERVICE", "DATABRICKS"])
                   else "Likely" if wa["weekend_pct"] > 30
                   else "No")
        if wa["overnight_pct"] > 30 and not any(t in wa["name"].upper() for t in ["ETL", "DBT", "SERVICE"]):
            action = "Investigate overnight"
        elif wa["weekend_pct"] > 30 and not any(t in wa["name"].upper() for t in ["ETL", "DBT", "SERVICE"]):
            action = "Review weekend jobs"
        elif wa["overnight_pct"] > 10:
            action = "Check auto-suspend"
        else:
            action = "OK"
        wknd_rows.append([
            wa["name"], f"{wa['total_credits_90d']:,.0f}",
            f"{wa['weekend_pct']:.0f}%", f"{wa['overnight_pct']:.0f}%",
            is_auto, action,
        ])
    story.append(_tbl(wknd_rows, [1.65 * inch, 0.8 * inch, 0.75 * inch, 1.1 * inch, 0.9 * inch, 1.05 * inch]))
    story.append(PageBreak())

    # ══ PAGE 6: SIZING & MULTI-CLUSTER ════════════════════════════════════════
    story.append(Paragraph("Warehouse Sizing &amp; Multi-Cluster Candidacy", styles["HCSH"]))
    story.append(HRFlowable(width=content_w, thickness=1.5, color=SF_BLUE, spaceAfter=6))
    story.append(Paragraph(
        "The box plot shows the spread of hourly credit consumption per warehouse. "
        "A wide spread (tall box, high outliers) = bursty workload = strong multi-cluster signal. "
        "A narrow band at consistently low values = warehouse is <b>oversized</b> for its actual load.",
        styles["HCBody"]
    ))
    story.append(img_distrib)
    story.append(Spacer(1, 0.08 * inch))

    story.append(Paragraph("Sizing Analysis per Warehouse", styles["HCSubH"]))
    sizing_rows = [["Warehouse", "90d Cr", "Peak Hr", "p90 Hr", "p50 Hr", "CV", "Peak Size", "Typical", "Multi-Cluster?"]]
    for wa in list(wh_90.values())[:12]:
        mc_txt = "Yes — Enable" if wa["multi_cluster_signal"] else "No"
        sizing_rows.append([
            wa["name"],
            f"{wa['total_credits_90d']:,.0f}",
            f"{wa['max_hour_credits']:.1f}",
            f"{wa['p90_hour_credits']:.1f}",
            f"{wa['p50_hour_credits']:.1f}",
            f"{wa['cv']:.1f}",
            wa["peak_size_estimate"],
            wa["typical_size"],
            mc_txt,
        ])
    story.append(_tbl(sizing_rows, [1.5 * inch, 0.65 * inch, 0.65 * inch, 0.65 * inch, 0.65 * inch, 0.4 * inch, 0.7 * inch, 0.65 * inch, 0.8 * inch]))
    story.append(Spacer(1, 0.06 * inch))
    story.append(Paragraph(
        "CV = Coefficient of Variation (std/mean of hourly credits). CV > 1.5 = highly bursty. "
        "Size estimated from hourly credit rate: 1 cr/hr ≈ XS, 2 ≈ S, 4 ≈ M, 8 ≈ L, 16 ≈ XL.",
        styles["HCSmall"]
    ))
    story.append(PageBreak())

    # ══ PAGES 7–8: PER-WAREHOUSE RECOMMENDATIONS ══════════════════════════════
    story.append(Paragraph("Warehouse-Specific Optimization Recommendations", styles["HCSH"]))
    story.append(HRFlowable(width=content_w, thickness=1.5, color=SF_BLUE, spaceAfter=6))

    high_value = [wa for wa in wh_90.values() if wa["total_credits_90d"] > 50]
    for wa in high_value[:8]:
        recs = gen_warehouse_recs(wa)
        if recs:
            for title, bullets, badge in recs[:2]:
                _rec_block(story, title, bullets, styles, badge)
        else:
            story.append(Paragraph(
                f"<b>{wa['name']}</b>: Consumption pattern appears appropriate for its workload type. Continue monitoring.",
                styles["HCBody"]
            ))
    story.append(PageBreak())

    # ══ PAGES 9–11: SQL SCRIPTS ════════════════════════════════════════════════
    story.append(Paragraph("Query-Level Investigation: SQL Scripts", styles["HCSH"]))
    story.append(HRFlowable(width=content_w, thickness=1.5, color=SF_BLUE, spaceAfter=6))
    story.append(Paragraph(
        "Snowhouse metering shows <i>how much</i> each warehouse spends, but not <i>which queries</i> are driving cost. "
        "Run these in your Snowflake account (as ACCOUNTADMIN or with ACCOUNT_USAGE access) to pinpoint "
        "specific query patterns, repetitive workloads, and optimization candidates.",
        styles["HCBody"]
    ))

    # Determine the most-interesting warehouse for script #6
    # Use highest overnight% among top-5 if >20%, else top spender
    top5_wa = list(wh_90.values())[:5]
    night_suspects = [w for w in top5_wa if w["overnight_pct"] > 20]
    focus_wh = sorted(night_suspects, key=lambda x: x["overnight_pct"], reverse=True)[0]["name"] \
               if night_suspects else top5_wa[0]["name"] if top5_wa else "TARGET_WH"

    sql_sections = [
        ("1. Top Credit-Consuming Queries (Last 30 Days)", """\
SELECT warehouse_name,
       query_text,
       user_name,
       total_elapsed_time / 1000.0       AS elapsed_sec,
       credits_used_cloud_services        AS gcs_credits,
       partitions_scanned,
       partitions_total,
       ROUND(partitions_scanned * 100.0
             / NULLIF(partitions_total, 0), 1) AS pct_scanned,
       start_time
FROM   snowflake.account_usage.query_history
WHERE  start_time >= DATEADD('day', -30, CURRENT_DATE())
  AND  execution_status = 'SUCCESS'
  AND  total_elapsed_time > 0
ORDER  BY total_elapsed_time DESC
LIMIT  50;"""),

        ("2. Repetitive / Re-Running Queries (Same Hash, Many Executions)", """\
SELECT warehouse_name,
       query_hash,
       COUNT(*)                         AS execution_count,
       AVG(total_elapsed_time) / 1000.0 AS avg_elapsed_sec,
       SUM(total_elapsed_time) / 1000.0 AS total_elapsed_sec,
       MIN(query_text)                  AS sample_query
FROM   snowflake.account_usage.query_history
WHERE  start_time >= DATEADD('day', -30, CURRENT_DATE())
  AND  execution_status = 'SUCCESS'
GROUP  BY 1, 2
HAVING COUNT(*) > 20
ORDER  BY total_elapsed_sec DESC
LIMIT  30;"""),

        ("3. Queries NOT Benefiting from Result Cache (Redundant Executions)", """\
SELECT warehouse_name,
       query_hash,
       COUNT(*)                     AS executions,
       AVG(total_elapsed_time/1000) AS avg_sec,
       MIN(query_text)              AS sample_query
FROM   snowflake.account_usage.query_history
WHERE  start_time >= DATEADD('day', -30, CURRENT_DATE())
  AND  execution_status = 'SUCCESS'
  AND  is_client_generated_statement = FALSE
  AND  query_type = 'SELECT'
  AND  query_hash IN (
       SELECT query_hash FROM snowflake.account_usage.query_history
       WHERE  start_time >= DATEADD('day', -30, CURRENT_DATE())
       GROUP  BY query_hash HAVING COUNT(*) > 5
  )
GROUP  BY 1, 2
ORDER  BY executions DESC
LIMIT  20;"""),

        ("4. Warehouse Utilization by Hour of Day (Last 30 Days)", """\
SELECT warehouse_name,
       HOUR(start_time)                          AS hour_utc,
       COUNT(*)                                  AS query_count,
       AVG(total_elapsed_time / 1000.0)          AS avg_elapsed_sec,
       SUM(total_elapsed_time) / 3600000.0       AS total_elapsed_hrs
FROM   snowflake.account_usage.query_history
WHERE  start_time >= DATEADD('day', -30, CURRENT_DATE())
  AND  execution_status = 'SUCCESS'
  AND  warehouse_name IS NOT NULL
GROUP  BY 1, 2
ORDER  BY 1, 2;"""),

        ("5. Queries with Poor Partition Pruning (Full Table Scans)", """\
SELECT warehouse_name,
       user_name,
       query_text,
       partitions_scanned,
       partitions_total,
       ROUND(partitions_scanned * 100.0
             / NULLIF(partitions_total, 0), 1) AS pct_scanned,
       total_elapsed_time / 1000.0              AS elapsed_sec
FROM   snowflake.account_usage.query_history
WHERE  start_time >= DATEADD('day', -30, CURRENT_DATE())
  AND  execution_status = 'SUCCESS'
  AND  partitions_total > 100
  AND  partitions_scanned * 1.0 / NULLIF(partitions_total, 0) > 0.8
ORDER  BY partitions_scanned DESC
LIMIT  25;"""),

        (f"6. {focus_wh} — What Is It Running? (Targeted Investigation)", f"""\
-- Replace '{focus_wh}' with the exact warehouse name in your account
SELECT user_name,
       query_type,
       database_name,
       schema_name,
       query_text,
       total_elapsed_time / 1000.0  AS elapsed_sec,
       start_time
FROM   snowflake.account_usage.query_history
WHERE  warehouse_name = '{focus_wh}'
  AND  start_time >= DATEADD('day', -30, CURRENT_DATE())
  AND  execution_status = 'SUCCESS'
ORDER  BY total_elapsed_time DESC
LIMIT  50;"""),
    ]

    for section_title, sql in sql_sections:
        story.append(Spacer(1, 0.08 * inch))
        story.append(Paragraph(section_title, styles["HCSubH"]))
        for line in sql.strip().split("\n"):
            safe = line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            story.append(Paragraph(safe if safe.strip() else "&nbsp;", styles["HCCode"]))

    story.append(PageBreak())

    # ══ PAGE 12: PRIORITY ACTION MATRIX ═══════════════════════════════════════
    story.append(Paragraph("Priority Action Matrix", styles["HCSH"]))
    story.append(HRFlowable(width=content_w, thickness=1.5, color=SF_BLUE, spaceAfter=6))

    matrix_rows = build_priority_matrix(wa_results, styles)
    story.append(_para_tbl(matrix_rows, [0.25 * inch, 1.3 * inch, 3.9 * inch, 1.3 * inch]))
    story.append(Spacer(1, 0.1 * inch))
    story.append(Paragraph(
        f"Data Scope: Warehouse sizing and scheduling analysis uses Snowhouse internal metering "
        f"(account {account_id}, {deployment.upper()}). "
        f"Query-level detail (Scripts 1–6 above) requires direct access to ACCOUNT_USAGE views "
        f"in the customer's Snowflake account.",
        styles["HCSmall"]
    ))
    story.append(PageBreak())

    # ══ CONDITIONAL: CLUSTERING ════════════════════════════════════════════════
    if has_clustering:
        story.append(Paragraph("Automatic Clustering Analysis", styles["HCSH"]))
        story.append(HRFlowable(width=content_w, thickness=1.5, color=SF_BLUE, spaceAfter=10))
        story.append(Paragraph(
            f"Total clustering credits in analysis period: <b>{int(clust_total):,} (${clust_total*credit_rate:,.0f})</b>. "
            f"Review clustered tables using SYSTEM$CLUSTERING_INFORMATION and consider suspending or dropping "
            f"clustering keys on tables that are not benefiting.",
            styles["HCBody"]
        ))
        if img_clust_bar: story.append(img_clust_bar)
        story.append(Spacer(1, 0.1 * inch))
        if img_clust_ent: story.append(img_clust_ent)
        story.append(Spacer(1, 0.1 * inch))
        story.append(Paragraph("Recommended Actions", styles["HCSubH"]))
        for rec in [
            "Run: SELECT table_catalog, table_schema, table_name, clustering_key FROM information_schema.tables WHERE clustering_key IS NOT NULL",
            "For each table: SELECT SYSTEM$CLUSTERING_INFORMATION('db.schema.table') — check average_depth",
            "Suspend non-beneficial: ALTER TABLE ... SUSPEND RECLUSTER",
            "Drop unused: ALTER TABLE ... DROP CLUSTERING KEY",
        ]:
            story.append(Paragraph(f"&#x2192; {rec}", ParagraphStyle(
                "HCClustRec", fontName="Helvetica", fontSize=9, leading=13,
                textColor=SF_DARK, spaceAfter=4, leftIndent=10)))
        story.append(PageBreak())

    # ══ CONDITIONAL: AI/CORTEX ═════════════════════════════════════════════════
    if has_ai:
        story.append(Paragraph("AI &amp; Cortex Adoption", styles["HCSH"]))
        story.append(HRFlowable(width=content_w, thickness=1.5, color=SF_BLUE, spaceAfter=10))
        if img_ai_stack: story.append(img_ai_stack)
        story.append(Spacer(1, 0.1 * inch))
        ai_recent = ai.groupby("service")["CREDITS"].sum().sort_values(ascending=False)
        ai_rows   = [["Cortex Product", "Credits (12-mo)", "Est. Cost"]]
        for svc, cr in ai_recent.items():
            if cr > 0.01:
                ai_rows.append([svc, f"{cr:,.1f}", f"${cr*credit_rate:,.0f}"])
        story.append(_tbl(ai_rows, [2.0 * inch, 1.2 * inch, 1.0 * inch]))
        story.append(PageBreak())
    else:
        # Nudge toward AI adoption
        story.append(Paragraph("AI &amp; Cortex Adoption", styles["HCSH"]))
        story.append(HRFlowable(width=content_w, thickness=1.5, color=SF_BLUE, spaceAfter=6))
        story.append(Paragraph(
            "No AI/Cortex usage detected in the analysis period. Snowflake Cortex offers "
            "purpose-built AI products that run natively on your existing data with no data movement: "
            "<b>Cortex Analyst</b> for natural-language SQL, <b>Cortex Search</b> for semantic search, "
            "<b>Snowflake Intelligence</b> for conversational analytics, and <b>AI functions</b> for "
            "in-pipeline classification, extraction, and summarization.",
            styles["HCBody"]
        ))
        story.append(PageBreak())

    # ══ INFRASTRUCTURE OVERVIEW ═════════════════════════════════════════════════
    story.append(Paragraph("Infrastructure Overview", styles["HCSH"]))
    story.append(HRFlowable(width=content_w, thickness=1.5, color=SF_BLUE, spaceAfter=6))
    if img_daily:
        story.append(img_daily)
        story.append(Spacer(1, 0.12 * inch))
    if img_storage:
        story.append(img_storage)
    story.append(PageBreak())

    # ══ APPENDIX ════════════════════════════════════════════════════════════════
    story.append(Paragraph("Appendix: Warehouse Details", styles["HCSH"]))
    story.append(HRFlowable(width=content_w, thickness=1.5, color=SF_BLUE, spaceAfter=10))

    # 12-month credits per warehouse
    recent_start = sorted(all_months)[-4] if len(all_months) >= 4 else start_month
    wh_recent = wh[wh["MONTH"] >= recent_start].groupby("warehouse")["CREDITS"].sum().sort_values(ascending=False)
    wh_total_sum = wh_recent.sum()

    reverse_map = {v: k for k, v in wh_map.items()}
    app_rows = [["Warehouse", "Entity ID", "Credits (Recent 4 mo)", "Est. Cost", "% of Compute"]]
    for wname, credits in wh_recent.head(25).items():
        eid_val = reverse_map.get(wname, "—")
        pct = credits / wh_total_sum * 100 if wh_total_sum > 0 else 0
        app_rows.append([wname, str(eid_val), f"{credits:,.0f}", f"${credits*credit_rate:,.0f}", f"{pct:.1f}%"])
    story.append(_tbl(app_rows, [2.0 * inch, 1.3 * inch, 1.3 * inch, 0.85 * inch, 0.8 * inch]))

    print(f"Building PDF: {out_path}")
    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)
    print(f"Done! Report saved to:\n{out_path}")


# ── MAIN ───────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Generate Snowflake deep-dive cost health check PDF from Snowhouse")
    parser.add_argument("--account-id",    required=True, type=int,   help="Snowhouse account ID")
    parser.add_argument("--deployment",    required=True,             help="Deployment (va2, va3, prod3, etc.)")
    parser.add_argument("--credit-rate",   required=True, type=float, help="$/credit rate")
    parser.add_argument("--company-name",  required=True,             help="Company name for PDF branding")
    parser.add_argument("--warehouse-map", default=None,              help="Path to JSON file or inline JSON: {entity_id: name}")
    parser.add_argument("--output",        default=None,              help="Output PDF path")
    parser.add_argument("--months-back",   type=int, default=12,      help="Months of history to pull (default: 12)")
    parser.add_argument("--locator",       default="",                help="Account locator for cover page")
    args = parser.parse_args()

    # Load warehouse map
    wh_map = {}
    if args.warehouse_map:
        if os.path.isfile(args.warehouse_map):
            with open(args.warehouse_map) as f:
                wh_map = json.load(f)
        else:
            try:
                wh_map = json.loads(args.warehouse_map)
            except json.JSONDecodeError:
                print(f"WARNING: Could not parse warehouse map: {args.warehouse_map}")

    output_path = args.output or f"./{args.company_name.replace(' ', '_')}_Health_Check.pdf"

    config = dict(
        account_id   = args.account_id,
        deployment   = args.deployment,
        credit_rate  = args.credit_rate,
        company_name = args.company_name,
        warehouse_map= wh_map,
        output       = output_path,
        locator      = args.locator or str(args.account_id),
    )

    print(f"Connecting to Snowhouse ({args.deployment.upper()}, account {args.account_id})...")
    conn = get_conn()
    wh, clust, tasks, gs, ai, st, daily = pull_data(conn, args.account_id, args.deployment, args.months_back)
    wh_hourly = pull_hourly_data(conn, args.account_id, args.deployment)
    conn.close()

    print("Analyzing warehouse patterns...")
    wa_results = analyze_warehouses(wh_hourly, wh, wh_map)
    print(f"  Found {len(wa_results)} active warehouses in hourly data")

    print("Building PDF...")
    build_pdf(wh, wh_hourly, clust, tasks, gs, ai, st, daily, wa_results, config)


if __name__ == "__main__":
    main()
