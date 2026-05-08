#!/usr/bin/env python3
"""
generate_report.py — Pull Snowhouse metering data and generate a cost health check PDF.

Usage:
    SNOWFLAKE_CONNECTION_NAME=SNOWHOUSE_AWS_US_WEST_2 uv run --project <SKILL_DIR> \
        python <SKILL_DIR>/scripts/generate_report.py \
        --account-id 7780103 \
        --deployment va3 \
        --credit-rate 1.78 \
        --company-name "BMG360" \
        --warehouse-map warehouse_map.json \
        --output "BMG360_Health_Check.pdf"
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
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import matplotlib.dates as mdates
import matplotlib.patches as mpatches
import numpy as np
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
SF_BLUE   = colors.Color(0.043, 0.333, 0.698)
SF_CYAN   = colors.Color(0.0,   0.749, 0.925)
SF_GREY   = colors.Color(0.933, 0.933, 0.933)
SF_DARK   = colors.Color(0.133, 0.133, 0.133)

PALETTE = ["#0757B2", "#00BEec", "#25B57C", "#F99A0F", "#DB3030",
           "#8A4EAE", "#5B9BD5", "#70AD47", "#FF7043", "#AB47BC",
           "#26C6DA", "#EC407A", "#78909C", "#66BB6A", "#FFA726"]

AI_FRIENDLY = {
    "AI_SERVICE_CORTEX_SEARCH":                            "Cortex Search",
    "AI_SERVICE_CORTEX_ANALYST_MESSAGE":                   "Cortex Analyst",
    "AI_SERVICE_CORTEX_FUNCTION_UNSEGMENTED_TOKENS":       "Cortex Functions",
    "AI_SERVICE_CORTEX_FUNCTION_INPUT_TOKENS":             "Cortex Functions",
    "AI_SERVICE_CORTEX_FUNCTION_OUTPUT_TOKENS":            "Cortex Functions",
    "AI_SERVICE_CORTEX_FUNCTION":                          "Cortex Functions",
    "AI_SERVICE_AGENT_INPUT_TOKENS":                       "Cortex Agents",
    "AI_SERVICE_AGENT_OUTPUT_TOKENS":                      "Cortex Agents",
    "AI_SERVICE_AGENT_CACHE_WRITE_TOKENS":                 "Cortex Agents",
    "AI_SERVICE_AGENT_CACHE_READ_TOKENS":                  "Cortex Agents",
    "AI_SERVICE_AGENT_ANALYST_INPUT_TOKENS":               "Cortex Agents",
    "AI_SERVICE_AGENT_ANALYST_OUTPUT_TOKENS":              "Cortex Agents",
    "AI_SERVICE_SNOWFLAKE_INTELLIGENCE_INPUT_TOKENS":      "Snowflake Intelligence",
    "AI_SERVICE_SNOWFLAKE_INTELLIGENCE_OUTPUT_TOKENS":     "Snowflake Intelligence",
    "AI_SERVICE_SNOWFLAKE_INTELLIGENCE_CACHE_WRITE_TOKENS":"Snowflake Intelligence",
    "AI_SERVICE_SNOWFLAKE_INTELLIGENCE_CACHE_READ_TOKENS": "Snowflake Intelligence",
    "AI_SERVICE_CORTEX_DOCUMENT_FUNCTION":                 "Document AI",
    "AI_SERVICE_CORTEX_FINETUNING":                        "Fine-Tuning",
}


def get_conn():
    return snowflake.connector.connect(
        connection_name=os.getenv("SNOWFLAKE_CONNECTION_NAME") or "SNOWHOUSE_AWS_US_WEST_2"
    )


def query(conn, sql):
    cur = conn.cursor()
    cur.execute(sql)
    cols = [d[0] for d in cur.description]
    rows = cur.fetchall()
    df = pd.DataFrame(rows, columns=cols)
    for col in df.columns:
        if df[col].apply(lambda x: isinstance(x, decimal.Decimal)).any():
            df[col] = df[col].apply(lambda x: float(x) if isinstance(x, decimal.Decimal) else x)
    return df


def pull_data(conn, account_id, deployment, months_back):
    dep = deployment.upper()
    db = f"METERING_BY_HOUR_{dep}.METERING"

    print("Pulling warehouse metering...")
    wh = query(conn, f"""
        SELECT entity_id, DATE_TRUNC('month', usage_time) AS month, SUM(credits) AS credits
        FROM {db}.WAREHOUSE_METERING
        WHERE account_id = {account_id} AND usage_time >= DATEADD('month', -{months_back}, CURRENT_DATE())
        GROUP BY 1, 2
    """)
    wh["MONTH"] = pd.to_datetime(wh["MONTH"]).dt.tz_localize(None)

    print("Pulling clustering metering...")
    clust = query(conn, f"""
        SELECT entity_id, DATE_TRUNC('month', usage_time) AS month, SUM(credits) AS credits
        FROM {db}.COMPUTE_SERVICE_METERING
        WHERE account_id = {account_id} AND event_type = 'COMPUTE_SERVICE_CLUSTERING'
          AND usage_time >= DATEADD('month', -{months_back}, CURRENT_DATE())
        GROUP BY 1, 2
    """)
    clust["MONTH"] = pd.to_datetime(clust["MONTH"]).dt.tz_localize(None)

    print("Pulling scheduled tasks metering...")
    tasks = query(conn, f"""
        SELECT DATE_TRUNC('month', usage_time) AS month, SUM(credits) AS credits
        FROM {db}.COMPUTE_SERVICE_METERING
        WHERE account_id = {account_id} AND event_type = 'COMPUTE_SERVICE_USER_SCHEDULED_TASK'
          AND usage_time >= DATEADD('month', -{months_back}, CURRENT_DATE())
        GROUP BY 1
    """)
    tasks["MONTH"] = pd.to_datetime(tasks["MONTH"]).dt.tz_localize(None)

    print("Pulling cloud services metering...")
    gs = query(conn, f"""
        SELECT DATE_TRUNC('month', usage_time) AS month, SUM(credits) AS credits
        FROM {db}.GS_METERING
        WHERE account_id = {account_id} AND usage_time >= DATEADD('month', -{months_back}, CURRENT_DATE())
        GROUP BY 1
    """)
    gs["MONTH"] = pd.to_datetime(gs["MONTH"]).dt.tz_localize(None)

    print("Pulling AI/Cortex metering...")
    ai = query(conn, f"""
        SELECT event_type, DATE_TRUNC('month', usage_time) AS month, SUM(credits) AS credits
        FROM {db}.AI_SERVICES_METERING
        WHERE account_id = {account_id} AND usage_time >= DATEADD('month', -{months_back}, CURRENT_DATE())
        GROUP BY 1, 2
    """)
    ai["service"] = ai["EVENT_TYPE"].map(AI_FRIENDLY).fillna("Other AI")
    ai["MONTH"] = pd.to_datetime(ai["MONTH"]).dt.tz_localize(None)

    print("Pulling storage metering...")
    st = query(conn, f"""
        SELECT usage_date::DATE AS day, SUM(bytes) / POW(1024,4) AS storage_tb
        FROM {db}.STORAGE_SIMPLE_AVG_METERING
        WHERE account_id = {account_id} AND usage_date >= DATEADD('month', -{months_back}, CURRENT_DATE())
        GROUP BY 1 ORDER BY 1
    """)
    st["DAY"] = pd.to_datetime(st["DAY"]).dt.tz_localize(None)

    print("Pulling daily compute (last 90 days)...")
    daily = query(conn, f"""
        SELECT DATE_TRUNC('day', usage_time) AS day, SUM(credits) AS warehouse_credits
        FROM {db}.WAREHOUSE_METERING
        WHERE account_id = {account_id} AND usage_time >= DATEADD('day', -90, CURRENT_DATE())
        GROUP BY 1 ORDER BY 1
    """)
    daily["DAY"] = pd.to_datetime(daily["DAY"]).dt.tz_localize(None)

    return wh, clust, tasks, gs, ai, st, daily


# ── CHART HELPERS ─────────────────────────────────────────────────────────────
def fig_to_image(fig, width=7.0*inch):
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    buf.seek(0)
    plt.close(fig)
    from PIL import Image as PILImage
    pil_img = PILImage.open(buf)
    pw, ph = pil_img.size
    aspect = ph / pw
    img = Image(buf)
    img.drawWidth = width
    img.drawHeight = width * aspect
    return img


def fmt_dollar(v):
    if v >= 1_000_000: return f"${v/1_000_000:.1f}M"
    if v >= 1_000: return f"${v/1_000:.1f}K"
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
    ax.set_xticks(x); ax.set_xticklabels(months, fontsize=8.5)
    ax.set_ylabel("Credits", fontsize=9)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:,.0f}"))
    ax.legend(fontsize=8, loc="upper left", framealpha=0.85)
    ax.set_title("Monthly Credit Consumption by Service Type", fontsize=11, fontweight="bold", pad=10)
    ax.spines["top"].set_visible(False); ax.spines["right"].set_visible(False)
    ax.set_axisbelow(True); ax.yaxis.grid(True, linestyle="--", alpha=0.4)
    fig.tight_layout()
    return fig_to_image(fig, width=6.8*inch)


def chart_warehouse_trends(wh, start_month):
    wh_mo = wh.groupby(["MONTH", "warehouse"])["CREDITS"].sum().reset_index()
    top_wh = wh_mo.groupby("warehouse")["CREDITS"].sum().nlargest(8).index.tolist()
    pivot = wh_mo[wh_mo["warehouse"].isin(top_wh)].pivot(index="MONTH", columns="warehouse", values="CREDITS").fillna(0).sort_index()
    pivot = pivot[pivot.index >= start_month]

    fig, ax = plt.subplots(figsize=(10, 4))
    for i, col in enumerate(pivot.columns):
        ax.plot(pivot.index, pivot[col], marker="o", markersize=4, color=PALETTE[i % len(PALETTE)], label=col, linewidth=2)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
    ax.xaxis.set_major_locator(mdates.MonthLocator())
    plt.setp(ax.xaxis.get_majorticklabels(), rotation=30, ha="right", fontsize=8)
    ax.set_ylabel("Credits", fontsize=9)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:,.0f}"))
    ax.legend(fontsize=7.5, loc="upper left", framealpha=0.85, ncol=2)
    ax.set_title("Top 8 Warehouse Credit Consumption — Monthly", fontsize=11, fontweight="bold", pad=10)
    ax.spines["top"].set_visible(False); ax.spines["right"].set_visible(False)
    ax.set_axisbelow(True); ax.yaxis.grid(True, linestyle="--", alpha=0.4)
    fig.tight_layout()
    return fig_to_image(fig, width=6.8*inch)


def chart_clustering_monthly(clust_mo, start_month):
    cm = clust_mo[clust_mo.index >= start_month]
    months = [d.strftime("%b %Y") for d in cm.index]
    x = np.arange(len(months))
    bar_colors = [PALETTE[4] if v > 500 else PALETTE[0] for v in cm.values]

    fig, ax = plt.subplots(figsize=(10, 3.5))
    bars = ax.bar(x, cm.values, color=bar_colors, width=0.65, edgecolor="white", linewidth=0.4)
    for bar, val in zip(bars, cm.values):
        if val > 5:
            ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 10, f"{val:,.0f}", ha="center", va="bottom", fontsize=8)
    ax.set_xticks(x); ax.set_xticklabels(months, fontsize=8.5)
    ax.set_ylabel("Credits", fontsize=9)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:,.0f}"))
    ax.set_title("Automatic Clustering Credits by Month", fontsize=11, fontweight="bold", pad=10)
    ax.spines["top"].set_visible(False); ax.spines["right"].set_visible(False)
    ax.set_axisbelow(True); ax.yaxis.grid(True, linestyle="--", alpha=0.4)
    fig.tight_layout()
    return fig_to_image(fig, width=6.8*inch)


def chart_clustering_entities(clust, credit_rate):
    clust_recent = clust[clust["MONTH"] >= clust["MONTH"].max() - pd.DateOffset(months=3)]
    by_entity = clust_recent.groupby("ENTITY_ID")["CREDITS"].sum().sort_values(ascending=False).head(10)
    labels = [f"Table ...{str(eid)[-8:]}" for eid in by_entity.index]

    fig, ax = plt.subplots(figsize=(9, 4))
    y = np.arange(len(labels))
    bar_colors = [PALETTE[4] if v > 500 else PALETTE[0] for v in by_entity.values]
    bars = ax.barh(y, by_entity.values, color=bar_colors, edgecolor="white", linewidth=0.4)
    for bar, val in zip(bars, by_entity.values):
        ax.text(bar.get_width() + 5, bar.get_y() + bar.get_height()/2,
                f"{val:,.0f} cr (${val*credit_rate:,.0f})", va="center", fontsize=8)
    ax.set_yticks(y); ax.set_yticklabels(labels, fontsize=8.5); ax.invert_yaxis()
    ax.set_xlabel("Credits", fontsize=9)
    ax.set_title("Top Clustering Table Entities (Last 3 Months)", fontsize=11, fontweight="bold", pad=10)
    ax.spines["top"].set_visible(False); ax.spines["right"].set_visible(False)
    ax.set_axisbelow(True); ax.xaxis.grid(True, linestyle="--", alpha=0.4)
    fig.tight_layout()
    return fig_to_image(fig, width=6.5*inch)


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
    return fig_to_image(fig, width=6.8*inch)


def chart_daily(daily):
    df = daily.sort_values("DAY").copy()
    fig, ax = plt.subplots(figsize=(10, 3))
    ax.fill_between(df["DAY"], df["WAREHOUSE_CREDITS"], alpha=0.22, color=PALETTE[0])
    ax.plot(df["DAY"], df["WAREHOUSE_CREDITS"], color=PALETTE[0], linewidth=1.5)
    df["rolling"] = df["WAREHOUSE_CREDITS"].rolling(7, min_periods=1).mean()
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
    return fig_to_image(fig, width=6.8*inch)


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
            ax.text(bar.get_x() + bar.get_width()/2, h, f"{h:.1f}", ha="center", va="bottom", fontsize=7)
    fig.tight_layout()
    return fig_to_image(fig, width=6.8*inch)


# ── PDF STYLES ────────────────────────────────────────────────────────────────
def make_styles():
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle("Cover1", fontName="Helvetica-Bold", fontSize=28, leading=34, textColor=colors.white, alignment=TA_LEFT))
    styles.add(ParagraphStyle("Cover2", fontName="Helvetica", fontSize=14, leading=20, textColor=colors.Color(0.8,0.9,1.0), alignment=TA_LEFT))
    styles.add(ParagraphStyle("Cover3", fontName="Helvetica", fontSize=10, leading=14, textColor=colors.Color(0.7,0.8,0.9), alignment=TA_LEFT))
    styles.add(ParagraphStyle("SectionH", fontName="Helvetica-Bold", fontSize=14, leading=20, textColor=SF_BLUE, spaceBefore=16, spaceAfter=4))
    styles.add(ParagraphStyle("SubH", fontName="Helvetica-Bold", fontSize=10, leading=14, textColor=SF_DARK, spaceBefore=8, spaceAfter=2))
    styles.add(ParagraphStyle("Body", fontName="Helvetica", fontSize=9, leading=13, textColor=SF_DARK, spaceAfter=6))
    styles.add(ParagraphStyle("BodySmall", fontName="Helvetica", fontSize=8, leading=12, textColor=colors.Color(0.4,0.4,0.4)))
    styles.add(ParagraphStyle("Alert", fontName="Helvetica-Bold", fontSize=9, leading=13, textColor=colors.Color(0.7,0.1,0.1), backColor=colors.Color(1.0,0.95,0.95)))
    return styles


def kpi_table(kpis, styles):
    cells = []
    for label, value, sub in kpis:
        block = [
            Paragraph(f'<font size=17 color="#0757B2"><b>{value}</b></font>', styles["Body"]),
            Paragraph(f'<font size=8 color="#555">{label}</font>', styles["Body"]),
        ]
        if sub:
            block.append(Paragraph(f'<font size=7 color="#888">{sub}</font>', styles["BodySmall"]))
        cells.append(block)
    t = Table([cells], colWidths=[1.68*inch]*len(kpis))
    t.setStyle(TableStyle([
        ("BOX", (0,0), (-1,-1), 0.5, colors.Color(0.85,0.90,0.98)),
        ("INNERGRID", (0,0), (-1,-1), 0.5, colors.Color(0.85,0.90,0.98)),
        ("BACKGROUND", (0,0), (-1,-1), colors.Color(0.96,0.97,1.0)),
        ("VALIGN", (0,0), (-1,-1), "TOP"),
        ("TOPPADDING", (0,0), (-1,-1), 8), ("BOTTOMPADDING", (0,0), (-1,-1), 8),
        ("LEFTPADDING", (0,0), (-1,-1), 10),
    ]))
    return t


def section_table(rows, col_widths):
    t = Table(rows, colWidths=col_widths)
    t.setStyle(TableStyle([
        ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"), ("FONTSIZE", (0,0), (-1,-1), 8),
        ("BACKGROUND", (0,0), (-1,0), SF_BLUE), ("TEXTCOLOR", (0,0), (-1,0), colors.white),
        ("ALIGN", (1,0), (-1,-1), "RIGHT"), ("ALIGN", (0,0), (0,-1), "LEFT"),
        ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.white, colors.Color(0.96,0.97,1.0)]),
        ("GRID", (0,0), (-1,-1), 0.3, colors.Color(0.85,0.85,0.85)),
        ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
        ("TOPPADDING", (0,0), (-1,-1), 4), ("BOTTOMPADDING", (0,0), (-1,-1), 4),
        ("LEFTPADDING", (0,0), (-1,-1), 6),
    ]))
    return t


def build_pdf(wh, clust, tasks, gs, ai, st, daily, config):
    credit_rate = config["credit_rate"]
    company = config["company_name"]
    locator = config["locator"]
    deployment = config["deployment"]
    out_path = config["output"]
    wh_map = config["warehouse_map"]

    styles = make_styles()
    w, h = letter
    content_w = w - 1.3*inch
    report_date = datetime.now().strftime("%B %d, %Y")

    wh["warehouse"] = wh["ENTITY_ID"].map(lambda eid: wh_map.get(str(int(eid)), str(int(eid))))

    # compute series
    wh_mo = wh.groupby("MONTH")["CREDITS"].sum()
    clust_mo = clust.groupby("MONTH")["CREDITS"].sum() if not clust.empty else pd.Series(dtype=float)
    ai_mo = ai.groupby("MONTH")["CREDITS"].sum() if not ai.empty else pd.Series(dtype=float)
    gs_mo = gs.set_index("MONTH")["CREDITS"] if not gs.empty else pd.Series(dtype=float)
    tk_mo = tasks.set_index("MONTH")["CREDITS"] if not tasks.empty else pd.Series(dtype=float)

    all_months = sorted(set(wh_mo.index) | set(clust_mo.index) | set(ai_mo.index) | set(gs_mo.index))
    start_month = min(all_months) if all_months else pd.Timestamp("2025-01-01")

    def total_mo(mo):
        return wh_mo.get(mo, 0) + clust_mo.get(mo, 0) + ai_mo.get(mo, 0) + gs_mo.get(mo, 0) + tk_mo.get(mo, 0)

    last_full = sorted(all_months)[-2] if len(all_months) >= 2 else all_months[-1] if all_months else pd.Timestamp.now()
    prev_full = sorted(all_months)[-3] if len(all_months) >= 3 else last_full

    last_total = total_mo(last_full)
    prev_total = total_mo(prev_full)
    mom_pct = ((last_total - prev_total) / prev_total * 100) if prev_total else 0
    annualized = last_total * 12 * credit_rate

    ytd_start = pd.Timestamp(f"{datetime.now().year}-01-01")
    ytd_credits = sum(total_mo(mo) for mo in all_months if mo >= ytd_start)

    has_clustering = not clust.empty and clust["CREDITS"].sum() > 10
    has_ai = not ai.empty and ai["CREDITS"].sum() > 1
    clust_total = clust["CREDITS"].sum() if has_clustering else 0

    # warehouse 4-month breakdown
    recent_start = sorted(all_months)[-4] if len(all_months) >= 4 else start_month
    wh_recent = wh[wh["MONTH"] >= recent_start].groupby("warehouse")["CREDITS"].sum().sort_values(ascending=False)
    wh_total_sum = wh_recent.sum()

    # generate charts
    print("Generating charts...")
    img_monthly = chart_monthly_stacked(wh_mo, clust_mo, ai_mo, gs_mo, tk_mo, start_month)
    img_wh_trend = chart_warehouse_trends(wh, start_month)
    img_clust_bar = chart_clustering_monthly(clust_mo, start_month) if has_clustering else None
    img_clust_ent = chart_clustering_entities(clust, credit_rate) if has_clustering else None
    img_ai_stack = chart_ai_stacked(ai, start_month) if has_ai else None
    img_daily = chart_daily(daily) if not daily.empty else None
    img_storage = chart_storage(st, start_month) if not st.empty else None

    # ── Build PDF ──
    doc = SimpleDocTemplate(out_path, pagesize=letter, topMargin=0.65*inch, bottomMargin=0.45*inch, leftMargin=0.65*inch, rightMargin=0.65*inch)

    def on_page(canvas, doc_obj):
        canvas.saveState()
        if doc_obj.page > 1:
            canvas.setFillColor(SF_BLUE)
            canvas.rect(0, h - 0.45*inch, w, 0.45*inch, fill=1, stroke=0)
            canvas.setFillColor(colors.white)
            canvas.setFont("Helvetica-Bold", 9)
            canvas.drawString(0.5*inch, h - 0.28*inch, f"{company} — Snowflake Usage Health Check")
            canvas.setFont("Helvetica", 9)
            canvas.drawRightString(w - 0.5*inch, h - 0.28*inch, f"{report_date}  |  Page {doc_obj.page}")
            canvas.setFillColor(colors.Color(0.6,0.6,0.6))
            canvas.setFont("Helvetica", 7)
            canvas.drawCentredString(w/2, 0.25*inch, "Confidential — Prepared by Snowflake Account Team")
        canvas.restoreState()

    story = []

    # ══ PAGE 1: COVER ══
    cover_t = Table([[""]], colWidths=[content_w], rowHeights=[2.5*inch])
    cover_t.setStyle(TableStyle([("BACKGROUND", (0,0), (-1,-1), SF_BLUE)]))
    story.append(Spacer(1, 0.3*inch))
    story.append(cover_t)
    story.append(Spacer(1, 0.1*inch))
    story.append(Paragraph(company, styles["Cover1"]))
    story.append(Paragraph("Snowflake Usage &amp; Cost Health Check", styles["Cover2"]))
    story.append(Spacer(1, 0.15*inch))
    story.append(Paragraph(f"Prepared: {report_date}  |  Account: {locator}  |  Region: {deployment.upper()}", styles["Cover3"]))
    story.append(Paragraph(f"Credit Rate: ${credit_rate}/credit  |  Analysis Period: {start_month.strftime('%b %Y')} – {sorted(all_months)[-1].strftime('%b %Y')}", styles["Cover3"]))
    story.append(Spacer(1, 0.2*inch))
    accent = Table([[""]], colWidths=[content_w], rowHeights=[0.08*inch])
    accent.setStyle(TableStyle([("BACKGROUND", (0,0), (-1,-1), SF_CYAN)]))
    story.append(accent)
    story.append(Spacer(1, 0.25*inch))

    story.append(Paragraph("Key Highlights", ParagraphStyle("CH", fontName="Helvetica-Bold", fontSize=11, leading=15, textColor=SF_BLUE)))
    highlights = [
        f"<b>{int(ytd_credits):,} total credits</b> consumed YTD ({datetime.now().year}) — est. ${ytd_credits*credit_rate:,.0f}",
        f"<b>Annualized run rate: {fmt_dollar(annualized)}</b> based on {last_full.strftime('%b %Y')}",
        f"<b>{len(wh_recent)} active warehouses</b> — top spender: {wh_recent.index[0] if len(wh_recent) > 0 else 'N/A'} ({wh_recent.iloc[0]:,.0f} credits recent period)" if len(wh_recent) > 0 else "",
    ]
    if has_clustering:
        highlights.append(f"<b>Auto-clustering: {int(clust_total):,} credits (${clust_total*credit_rate:,.0f})</b> — optimization opportunity")
    if has_ai:
        ai_total = ai["CREDITS"].sum()
        highlights.append(f"<b>AI/Cortex active</b>: {ai_total:,.0f} credits across {ai['service'].nunique()} products")

    for hl in highlights:
        if hl:
            story.append(Paragraph(f"• &nbsp; {hl}", ParagraphStyle("HL", fontName="Helvetica", fontSize=9.5, leading=15, textColor=SF_DARK, spaceAfter=5, leftIndent=8)))

    story.append(PageBreak())

    # ══ PAGE 2: EXECUTIVE SUMMARY ══
    story.append(Paragraph("Executive Summary", styles["SectionH"]))
    story.append(HRFlowable(width=content_w, thickness=1.5, color=SF_BLUE, spaceAfter=10))

    kpis = [
        (f"YTD Credits ({datetime.now().year})", f"{int(ytd_credits):,}", f"${ytd_credits*credit_rate:,.0f} est."),
        (f"{last_full.strftime('%b %Y')} Credits", f"{int(last_total):,}", f"${last_total*credit_rate:,.0f} est."),
        ("MoM Change", f"{mom_pct:+.0f}%", f"{prev_full.strftime('%b')}→{last_full.strftime('%b')}"),
        ("Annualized Run Rate", fmt_dollar(annualized), f"Based on {last_full.strftime('%b %Y')}"),
    ]
    if has_clustering:
        kpis.append(("Clustering Spend", f"{int(clust_total):,} cr", f"${clust_total*credit_rate:,.0f}"))
    story.append(kpi_table(kpis, styles))
    story.append(Spacer(1, 0.15*inch))
    story.append(img_monthly)

    # monthly table
    monthly_rows = [["Month", "Compute", "Clustering", "AI/Cortex", "Cloud Svcs", "Tasks", "Total", "Est. Cost"]]
    for mo in sorted(all_months, reverse=True):
        w_ = wh_mo.get(mo, 0); c_ = clust_mo.get(mo, 0); a_ = ai_mo.get(mo, 0)
        g_ = gs_mo.get(mo, 0); t_ = tk_mo.get(mo, 0); tot = w_+c_+a_+g_+t_
        monthly_rows.append([mo.strftime("%b %Y"), f"{w_:,.0f}", f"{c_:,.0f}", f"{a_:,.0f}", f"{g_:,.0f}", f"{t_:,.0f}", f"{tot:,.0f}", f"${tot*credit_rate:,.0f}"])
    col_ws = [0.82*inch]*8
    story.append(Paragraph("Monthly Credit Summary", styles["SubH"]))
    story.append(section_table(monthly_rows, col_ws))
    story.append(PageBreak())

    # ══ PAGE 3: WAREHOUSE ANALYSIS ══
    story.append(Paragraph("Warehouse Compute Analysis", styles["SectionH"]))
    story.append(HRFlowable(width=content_w, thickness=1.5, color=SF_BLUE, spaceAfter=10))
    story.append(img_wh_trend)
    story.append(Spacer(1, 0.1*inch))

    wh_rows = [["Warehouse", "Credits (Recent)", "Est. Cost", "% of Compute"]]
    for wname, credits in wh_recent.head(20).items():
        pct = credits / wh_total_sum * 100 if wh_total_sum > 0 else 0
        wh_rows.append([wname, f"{credits:,.0f}", f"${credits*credit_rate:,.0f}", f"{pct:.1f}%"])
    story.append(section_table(wh_rows, [2.2*inch, 1.1*inch, 1.0*inch, 0.9*inch]))
    story.append(PageBreak())

    # ══ PAGE 4: CLUSTERING (conditional) ══
    if has_clustering:
        story.append(Paragraph("Automatic Clustering Analysis", styles["SectionH"]))
        story.append(HRFlowable(width=content_w, thickness=1.5, color=SF_BLUE, spaceAfter=10))
        story.append(Paragraph(
            f"Total clustering credits in analysis period: <b>{int(clust_total):,} (${clust_total*credit_rate:,.0f})</b>. "
            f"Review clustered tables using SYSTEM$CLUSTERING_INFORMATION and consider suspending or dropping "
            f"clustering keys on tables that are not benefiting.",
            styles["Body"]))
        if img_clust_bar: story.append(img_clust_bar)
        story.append(Spacer(1, 0.1*inch))
        if img_clust_ent: story.append(img_clust_ent)
        story.append(Spacer(1, 0.1*inch))
        story.append(Paragraph("Recommended Actions", styles["SubH"]))
        for rec in [
            "Run: SELECT table_catalog, table_schema, table_name, clustering_key FROM information_schema.tables WHERE clustering_key IS NOT NULL",
            "For each: SELECT SYSTEM$CLUSTERING_INFORMATION('db.schema.table') — check average_depth",
            "Suspend non-beneficial: ALTER TABLE ... SUSPEND RECLUSTER",
            "Drop unused: ALTER TABLE ... DROP CLUSTERING KEY",
        ]:
            story.append(Paragraph(f"→  {rec}", ParagraphStyle("R", fontName="Helvetica", fontSize=9, leading=13, textColor=SF_DARK, spaceAfter=4, leftIndent=10)))
        story.append(PageBreak())

    # ══ PAGE 5: AI (conditional) ══
    if has_ai:
        story.append(Paragraph("AI &amp; Cortex Adoption", styles["SectionH"]))
        story.append(HRFlowable(width=content_w, thickness=1.5, color=SF_BLUE, spaceAfter=10))
        if img_ai_stack: story.append(img_ai_stack)
        story.append(Spacer(1, 0.1*inch))
        ai_recent = ai.groupby("service")["CREDITS"].sum().sort_values(ascending=False)
        ai_rows = [["Cortex Product", "Credits (12-mo)", "Est. Cost"]]
        for svc, cr in ai_recent.items():
            if cr > 0.01:
                ai_rows.append([svc, f"{cr:,.1f}", f"${cr*credit_rate:,.0f}"])
        story.append(section_table(ai_rows, [2.0*inch, 1.2*inch, 1.0*inch]))
        story.append(PageBreak())

    # ══ PAGE 6: INFRASTRUCTURE ══
    story.append(Paragraph("Infrastructure &amp; Storage", styles["SectionH"]))
    story.append(HRFlowable(width=content_w, thickness=1.5, color=SF_BLUE, spaceAfter=10))
    if img_daily: story.append(img_daily)
    story.append(Spacer(1, 0.12*inch))
    if img_storage: story.append(img_storage)
    story.append(PageBreak())

    # ══ PAGE 7: RECOMMENDATIONS ══
    story.append(Paragraph("Optimization Recommendations", styles["SectionH"]))
    story.append(HRFlowable(width=content_w, thickness=1.5, color=SF_BLUE, spaceAfter=10))

    recs = []
    if has_clustering:
        est_savings = clust_total * 4 * credit_rate * 0.7
        recs.append(("Audit Automatic Clustering", f"~{fmt_dollar(est_savings)}/year potential savings",
                     "Review all clustered tables. Suspend or drop clustering where ROI is not justified."))
    recs.append(("Warehouse Right-Sizing", "Variable savings",
                 "Evaluate top-spending warehouses for workload patterns. Consider multi-cluster for bursty workloads, larger sizes for long-running queries."))
    recs.append(("Auto-Suspend Tuning", "~5-10% compute reduction",
                 "Set AUTO_SUSPEND = 60s for bursty/AI warehouses. Ensure no warehouse runs idle for extended periods."))
    if not has_ai:
        recs.append(("Explore Cortex AI", "Platform ROI",
                     "No AI/Cortex usage detected. Consider Cortex Analyst for self-serve analytics or Snowflake Intelligence for conversational BI."))

    for title, impact, detail in recs:
        story.append(Paragraph(f"<b>{title}</b>", ParagraphStyle("RT", fontName="Helvetica-Bold", fontSize=10, leading=14, textColor=SF_BLUE, spaceBefore=10)))
        story.append(Paragraph(f"<i>Impact: {impact}</i>", ParagraphStyle("RI", fontName="Helvetica-Oblique", fontSize=8.5, leading=12, textColor=colors.Color(0.4,0.4,0.4), leftIndent=8)))
        story.append(Paragraph(detail, styles["Body"]))

    story.append(Spacer(1, 0.15*inch))
    story.append(HRFlowable(width=content_w, thickness=0.5, color=SF_GREY, spaceAfter=8))
    story.append(Paragraph("Data Scope Note", styles["SubH"]))
    story.append(Paragraph(
        f"This report uses Snowhouse internal metering ({deployment.upper()}, account ID {config['account_id']}). "
        f"Not included: user attribution, query text, table/clustering key names. "
        f"For deeper analysis, query ACCOUNT_USAGE views directly in the customer's account.",
        styles["Body"]))

    story.append(PageBreak())

    # ══ PAGE 8: APPENDIX ══
    story.append(Paragraph("Appendix: Warehouse Details", styles["SectionH"]))
    story.append(HRFlowable(width=content_w, thickness=1.5, color=SF_BLUE, spaceAfter=10))
    wh_app_rows = [["Warehouse", "Entity ID", "Credits", "Est. Cost", "% Total"]]
    reverse_map = {v: k for k, v in wh_map.items()}
    for wname, credits in wh_recent.items():
        eid = reverse_map.get(wname, "—")
        pct = credits / wh_total_sum * 100 if wh_total_sum > 0 else 0
        wh_app_rows.append([wname, str(eid), f"{credits:,.0f}", f"${credits*credit_rate:,.0f}", f"{pct:.1f}%"])
    story.append(section_table(wh_app_rows, [1.8*inch, 1.3*inch, 0.9*inch, 0.85*inch, 0.7*inch]))

    print(f"Building PDF: {out_path}")
    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)
    print(f"Done! Report saved to:\n{out_path}")


def main():
    parser = argparse.ArgumentParser(description="Generate Snowflake cost health check PDF from Snowhouse")
    parser.add_argument("--account-id", required=True, type=int, help="Snowhouse account ID (the 'id' field)")
    parser.add_argument("--deployment", required=True, help="Deployment (va2, va3, prod3, etc.)")
    parser.add_argument("--credit-rate", required=True, type=float, help="$/credit rate")
    parser.add_argument("--company-name", required=True, help="Company name for PDF branding")
    parser.add_argument("--warehouse-map", default=None, help="Path to JSON file: {entity_id: name}")
    parser.add_argument("--output", default=None, help="Output PDF path")
    parser.add_argument("--months-back", type=int, default=12, help="Months of history to pull")
    parser.add_argument("--locator", default="", help="Account locator for cover page")
    args = parser.parse_args()

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

    config = {
        "account_id": args.account_id,
        "deployment": args.deployment,
        "credit_rate": args.credit_rate,
        "company_name": args.company_name,
        "warehouse_map": wh_map,
        "output": output_path,
        "locator": args.locator or str(args.account_id),
    }

    conn = get_conn()
    wh, clust, tasks, gs, ai, st, daily = pull_data(conn, args.account_id, args.deployment, args.months_back)
    conn.close()

    build_pdf(wh, clust, tasks, gs, ai, st, daily, config)


if __name__ == "__main__":
    main()
