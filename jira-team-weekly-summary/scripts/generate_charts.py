"""
Jira Team Weekly Summary — Static PNG Chart Generator

Generates 4 static PNG images from Jira data, suitable for Confluence upload.
No JavaScript/dynamic rendering; all charts are matplotlib-generated PNGs.

Charts:
  1. chart_defect_matrix.png — assignee × issuetype matrix table (filter 37711)
  2. chart_fr_matrix.png       — assignee × project matrix table (filter 38267)
  3. chart_framework_pie.png   — project distribution pie chart (filter 106293)
  4. chart_xts_pie.png         — project distribution pie chart (filter 106624)

Usage:
    python scripts/generate_charts.py --data <data_json_file> --output-dir <dir>
"""

import json
import os
import sys
from datetime import datetime
from urllib.parse import quote
from collections import Counter

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
import matplotlib.ticker as mticker

# ─── Font setup for Chinese characters ───
# Try common Chinese fonts available on Windows
_CJK_FONT_NAMES = [
    "Microsoft YaHei",
    "SimHei",
    "Microsoft JhengHei",
    "SimSun",
    "KaiTi",
    "FangSong",
]

_chinese_font = None
for fname in _CJK_FONT_NAMES:
    for f in fm.fontManager.ttflist:
        if fname.lower() in f.name.lower():
            _chinese_font = f.name
            break
    if _chinese_font:
        break

if _chinese_font:
    plt.rcParams["font.family"] = _chinese_font
    print(f"Using Chinese font: {_chinese_font}")
else:
    print("WARNING: No Chinese font found. Chinese characters may render as boxes.")
    plt.rcParams["font.family"] = "sans-serif"
    # Fallback: try DejaVu Sans which supports some CJK
    plt.rcParams["font.sans-serif"] = ["DejaVu Sans"]

plt.rcParams["axes.unicode_minus"] = False  # Proper minus sign
matplotlib.rcParams["font.size"] = 10

# ─── Colors ───
PIE_COLORS = [
    "#4C78A8", "#F58518", "#E45756", "#72B7B2", "#54A24B",
    "#EECA3B", "#B279A2", "#FF9DA6", "#9D755D", "#BAB0AC",
]
OTHER_COLOR = "#D3D3D3"

TABLE_HEADER_BG = "#F4F5F7"
TABLE_HEADER_FG = "#42526E"
TABLE_TOTAL_BG = "#EBECF0"
TABLE_CELL_BORDER = "#DFE1E6"
LINK_COLOR = "#0052CC"


def build_jql_url(jira_url, filter_id, assignee=None, issuetype=None, project=None):
    """Build a clickable JQL hyperlink (as plain text for image annotation)."""
    params = [f"filter={filter_id}"]
    if assignee:
        params.append(f"assignee={quote(assignee, safe='')}")
    if issuetype:
        params.append(f"issuetype={quote(issuetype, safe='')}")
    if project:
        params.append(f"project={quote(project, safe='')}")
    return f"{jira_url}/issues/?jql={'%20AND%20'.join(params)}"


def build_matrix_table(section_data, row_field, col_field, col_label,
                       row_label, title, jira_url, filter_id, output_path):
    """Render a cross-tabulation matrix as a PNG image using matplotlib table."""
    matrix = section_data.get("matrix", {})

    # Collect all unique column values
    all_cols = set()
    for row_val, col_counts in matrix.items():
        all_cols.update(col_counts.keys())

    # Sort columns by total count descending
    col_totals = {col: sum(matrix.get(r, {}).get(col, 0) for r in matrix) for col in all_cols}
    sorted_cols = sorted(all_cols, key=lambda c: col_totals[c], reverse=True)

    # Sort rows by total count descending
    row_totals = {r: sum(matrix[r].values()) for r in matrix}
    sorted_rows = sorted(matrix.keys(), key=lambda r: row_totals[r], reverse=True)

    if not sorted_rows or not sorted_cols:
        fig, ax = plt.subplots(figsize=(8, 2))
        ax.text(0.5, 0.5, "(无数据)", ha="center", va="center", fontsize=14,
                transform=ax.transAxes)
        ax.axis("off")
        fig.savefig(output_path, dpi=120, bbox_inches="tight", facecolor="white")
        plt.close(fig)
        return

    # Determine link type
    link_type = "issuetype" if col_field == "issuetype" else "project"

    # Build cell text matrix (header row + data rows + total row)
    n_rows = len(sorted_rows) + 2  # header + data + total
    n_cols = len(sorted_cols) + 2  # row label + column values + total

    cell_text = []
    cell_colors = []
    data_for_links = []  # Store (row_idx, col_idx, url) for annotation

    # Header row
    header_row = [row_label] + list(sorted_cols) + ["合计"]
    cell_text.append(header_row)
    cell_colors.append([TABLE_HEADER_BG] * n_cols)

    # Data rows
    for row_idx, row_val in enumerate(sorted_rows):
        row_data = [str(row_val)]
        row_colors = [TABLE_HEADER_BG]
        for col_idx, col in enumerate(sorted_cols):
            count = matrix[row_val].get(col, 0)
            cell_text_val = str(count) if count > 0 else ""
            row_data.append(cell_text_val)
            row_colors.append("white")
            if count > 0:
                if link_type == "issuetype":
                    jql = build_jql_url(jira_url, filter_id, assignee=row_val, issuetype=col)
                else:
                    jql = build_jql_url(jira_url, filter_id, assignee=row_val, project=col)
                data_for_links.append((row_idx + 1, col_idx + 1, jql, count))
        # Total
        row_data.append(str(row_totals[row_val]))
        row_colors.append(TABLE_TOTAL_BG)
        cell_text.append(row_data)
        cell_colors.append(row_colors)

    # Total row
    total_row = ["合计"]
    total_colors = [TABLE_TOTAL_BG]
    grand_total = sum(row_totals.values())
    for col in sorted_cols:
        ct = col_totals[col]
        total_row.append(str(ct) if ct > 0 else "")
        total_colors.append(TABLE_TOTAL_BG)
    total_row.append(str(grand_total))
    total_colors.append(TABLE_TOTAL_BG)
    cell_text.append(total_row)
    cell_colors.append(total_colors)

    # Figure setup
    fig_width = max(10, n_cols * 1.2 + 3)
    fig_height = max(3, n_rows * 0.42 + 1.5)
    fig, ax = plt.subplots(figsize=(fig_width, fig_height))
    ax.axis("off")

    # Create table
    table = ax.table(
        cellText=cell_text,
        cellColours=cell_colors,
        cellLoc="center",
        loc="center",
    )

    # Style the table
    table.auto_set_font_size(False)
    table.set_fontsize(9)
    table.scale(1.0, 1.6)

    for (row, col), cell in table.get_celld().items():
        cell.set_edgecolor(TABLE_CELL_BORDER)
        cell.set_linewidth(0.5)

        # Bold header row and total row/col
        if row == 0 or row == len(sorted_rows) + 1:
            cell.get_text().set_fontweight("bold")
        if col == 0:
            cell.get_text().set_fontweight("bold")
            cell.set_text_props(ha="left")
            cell.PAD = 0.02
        if col == len(sorted_cols) + 1:
            cell.get_text().set_fontweight("bold")

        # Color links blue for non-zero cells with JQL
        for (dr, dc, jql, cnt) in data_for_links:
            if row == dr and col == dc:
                cell.get_text().set_color(LINK_COLOR)
                cell.get_text().set_fontweight("bold")

    # Title
    ax.set_title(title, fontsize=14, fontweight="bold", color="#172B4D", pad=20)

    # URL legend at bottom
    jql_url = build_jql_url(jira_url, filter_id)
    fig.text(0.5, 0.02, f"JQL: {jql_url}", ha="center", fontsize=7, color="#6B778C",
             style="italic")

    fig.savefig(output_path, dpi=150, bbox_inches="tight", facecolor="white", pad_inches=0.5)
    plt.close(fig)
    print(f"  矩阵表已保存: {output_path}")


def build_pie_chart(section_data, title, output_path):
    """Render a pie chart as PNG for project distribution (TOP 10 + Other)."""
    projects = section_data.get("projects", {})
    if not projects:
        fig, ax = plt.subplots(figsize=(8, 4))
        ax.text(0.5, 0.5, "(无数据)", ha="center", va="center", fontsize=14,
                transform=ax.transAxes)
        ax.axis("off")
        fig.savefig(output_path, dpi=120, bbox_inches="tight", facecolor="white")
        plt.close(fig)
        return

    # Sort and split TOP 10 vs Other
    sorted_items = sorted(projects.items(), key=lambda x: x[1], reverse=True)
    top_n = 10

    labels = []
    values = []
    colors = []

    for i, (proj, count) in enumerate(sorted_items):
        if i < top_n:
            labels.append(str(proj))
            values.append(count)
            colors.append(PIE_COLORS[i % len(PIE_COLORS)])

    # "Other" for remaining
    other_total = sum(v for _, v in sorted_items[top_n:])
    if other_total > 0:
        labels.append("其他")
        values.append(other_total)
        colors.append(OTHER_COLOR)

    total = sum(values)

    # Create figure
    fig, (ax_pie, ax_legend) = plt.subplots(1, 2, figsize=(14, 7),
                                             gridspec_kw={"width_ratios": [2, 1]})

    wedges, texts, autotexts = ax_pie.pie(
        values,
        labels=None,
        autopct=lambda pct: f"{pct:.1f}%" if pct > 2 else "",
        startangle=90,
        colors=colors,
        wedgeprops={"edgecolor": "white", "linewidth": 0.8},
        pctdistance=0.75,
    )

    for t in autotexts:
        t.set_fontsize(8)
        t.set_fontweight("bold")

    ax_pie.set_title(title, fontsize=14, fontweight="bold", color="#172B4D", pad=20)

    # Legend with count and percentage
    legend_labels = []
    for i, (label, value) in enumerate(zip(labels, values)):
        pct = value / total * 100
        legend_labels.append(f"{label}  ({value},  {pct:.1f}%)")

    ax_legend.axis("off")
    legend = ax_legend.legend(
        wedges, legend_labels,
        loc="center",
        fontsize=9,
        frameon=False,
        ncol=1,
    )

    fig.tight_layout()
    fig.savefig(output_path, dpi=150, bbox_inches="tight", facecolor="white", pad_inches=0.5)
    plt.close(fig)
    print(f"  饼图已保存: {output_path}")


def generate_all_charts(data, jira_url, output_dir):
    """Generate all 4 PNG charts from data.

    Expected data structure (same as generate_visuals.py):
    {
        "week_start": "2026-08-04",
        "week_end": "2026-08-11",
        "sections": {
            "resolved_defects": {
                "filter_id": 37711,
                "title": "问题解决 — 人员 × 问题类型",
                "matrix": {"user1": {"Bug": 3, "Defect": 2}, ...}
            },
            "fr_closed": {
                "filter_id": 38267,
                "title": "FR 闭环 — 人员 × 项目",
                "matrix": {"user1": {"PROJ_A": 5, "PROJ_B": 3}, ...}
            },
            "framework_analyzed": {
                "title": "框架分析 — 项目分布 TOP 10",
                "projects": {"FW_CORE": 8, "APP": 5, ...}
            },
            "xts_analyzed": {
                "title": "XTS 分析 — 项目分布 TOP 10",
                "projects": {"XTS_CORE": 6, "APP": 4, ...}
            }
        }
    }
    """
    os.makedirs(output_dir, exist_ok=True)
    sections = data.get("sections", {})
    week_start = data.get("week_start", "N/A")
    week_end = data.get("week_end", "N/A")

    outputs = {}

    # ── Chart 1: Defect matrix ──
    rd = sections.get("resolved_defects", {})
    path1 = os.path.join(output_dir, f"chart_defect_matrix_{week_start}.png")
    build_matrix_table(
        rd, "assignee", "issuetype", "问题类型", "经办人",
        f"一、问题解决 — 人员 × 问题类型（{week_start} 至 {week_end}）",
        jira_url, rd.get("filter_id", 37711), path1,
    )
    outputs["缺陷矩阵表"] = path1

    # ── Chart 2: FR matrix ──
    fr = sections.get("fr_closed", {})
    path2 = os.path.join(output_dir, f"chart_fr_matrix_{week_start}.png")
    build_matrix_table(
        fr, "assignee", "project", "项目", "经办人",
        f"二、FR 闭环 — 人员 × 项目（{week_start} 至 {week_end}）",
        jira_url, fr.get("filter_id", 38267), path2,
    )
    outputs["FR矩阵表"] = path2

    # ── Chart 3: Framework pie ──
    fw = sections.get("framework_analyzed", {})
    path3 = os.path.join(output_dir, f"chart_framework_pie_{week_start}.png")
    fw_title = f"三、框架分析 — 项目分布 TOP 10（{week_start} 至 {week_end}）"
    build_pie_chart(fw, fw_title, path3)
    outputs["框架饼图"] = path3

    # ── Chart 4: XTS pie ──
    xts = sections.get("xts_analyzed", {})
    path4 = os.path.join(output_dir, f"chart_xts_pie_{week_start}.png")
    xts_title = f"四、XTS 分析 — 项目分布 TOP 10（{week_start} 至 {week_end}）"
    build_pie_chart(xts, xts_title, path4)
    outputs["XTS饼图"] = path4

    # ── Save summary JSON ──
    summary = {
        "generated_at": datetime.now().isoformat(),
        "week_start": week_start,
        "week_end": week_end,
        "charts": outputs,
    }
    summary_path = os.path.join(output_dir, f"charts_summary_{week_start}.json")
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(f"\n图表生成摘要已保存: {summary_path}")

    return outputs


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Jira Team Weekly Static PNG Chart Generator")
    parser.add_argument("--data", "-d", required=True, help="JSON data file path")
    parser.add_argument("--output-dir", "-o", default="./reports",
                        help="Output directory for PNG files")
    parser.add_argument("--jira-url", default="https://jira.tcl.com",
                        help="Jira base URL for link annotations")
    args = parser.parse_args()

    with open(args.data, "r", encoding="utf-8") as f:
        data = json.load(f)

    outputs = generate_all_charts(data, args.jira_url, args.output_dir)

    print(f"\n生成 {len(outputs)} 个图表:")
    for chart_name, path in outputs.items():
        print(f"  {chart_name}: {path}")


if __name__ == "__main__":
    main()
