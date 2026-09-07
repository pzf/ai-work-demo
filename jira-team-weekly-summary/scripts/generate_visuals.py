"""
Jira Team Weekly Summary — Visual Chart Generator

Generate an HTML file with 4 visualization charts from Jira data.

Charts:
  1. 问题解决 — assignee × issuetype matrix table (filter 37711)
  2. FR 闭环 — assignee × project matrix table (filter 38267)
  3. 框架分析 — project distribution pie chart (filter 106293)
  4. XTS 分析 — project distribution pie chart (filter 106624)

Usage:
    python scripts/generate_visuals.py --data <data_json_file> --output <output_html>
"""

import json
import os
import sys
import html
from datetime import datetime
from urllib.parse import quote


# Chart.js pie chart colors (10 distinct hues + "Other" gray)
PIE_COLORS = [
    "#4C78A8", "#F58518", "#E45756", "#72B7B2", "#54A24B",
    "#EECA3B", "#B279A2", "#FF9DA6", "#9D755D", "#BAB0AC",
]
OTHER_COLOR = "#D3D3D3"


def build_jql_url(jira_url, filter_id, assignee=None, issuetype=None, project=None):
    """Build a clickable JQL hyperlink."""
    params = [f"filter={filter_id}"]
    if assignee:
        params.append(f"assignee={quote(assignee)}")
    if issuetype:
        params.append(f"issuetype={quote(issuetype)}")
    if project:
        params.append(f"project={quote(project)}")
    return f"{jira_url}/issues/?jql={'%20AND%20'.join(params)}"


def build_matrix_table(section_data, row_field, col_field, col_field_label,
                       row_label, jira_url, filter_id, link_type="issuetype"):
    """Build an HTML <table> for assignee × {col_field} cross-tabulation.

    Args:
        section_data: dict with "matrix" key -> {row_val: {col_val: count}}
        row_field: field name for rows (e.g. "assignee")
        col_field: field name for columns (e.g. "issuetype")
        col_field_label: display label for column header
        row_label: display label for row header
        jira_url: base Jira URL for building links
        filter_id: filter ID for building JQL links
        link_type: "issuetype" or "project" for JQL link param

    Returns:
        HTML string for the table
    """
    matrix = section_data.get("matrix", {})

    # Collect all unique column values
    all_cols = set()
    for row_val, col_counts in matrix.items():
        all_cols.update(col_counts.keys())

    # Sort columns by total count descending
    col_totals = {}
    for col in all_cols:
        col_totals[col] = sum(matrix.get(r, {}).get(col, 0) for r in matrix)
    sorted_cols = sorted(all_cols, key=lambda c: col_totals[c], reverse=True)

    # Sort rows by total count descending
    row_totals = {}
    for r in matrix:
        row_totals[r] = sum(matrix[r].values())
    sorted_rows = sorted(matrix.keys(), key=lambda r: row_totals[r], reverse=True)

    if not sorted_rows or not sorted_cols:
        return "<p>(无数据)</p>"

    # Build table
    table_html = '<table class="matrix-table">\n'

    # Header row
    table_html += f'  <tr><th class="row-header">{row_label}</th>'
    for col in sorted_cols:
        table_html += f'<th>{html.escape(col)}</th>'
    table_html += '<th class="total-col">合计</th></tr>\n'

    # Data rows
    for row_val in sorted_rows:
        table_html += f'  <tr><td class="row-header">{html.escape(row_val)}</td>'
        for col in sorted_cols:
            count = matrix[row_val].get(col, 0)
            if count > 0:
                if link_type == "issuetype":
                    jql_link = build_jql_url(jira_url, filter_id, assignee=row_val, issuetype=col)
                else:
                    jql_link = build_jql_url(jira_url, filter_id, assignee=row_val, project=col)
                table_html += f'<td><a href="{jql_link}" target="_blank">{count}</a></td>'
            else:
                table_html += '<td></td>'
        table_html += f'<td class="total-col">{row_totals[row_val]}</td></tr>\n'

    # Total row
    table_html += '  <tr class="total-row"><td class="row-header">合计</td>'
    grand_total = 0
    for col in sorted_cols:
        ct = col_totals[col]
        grand_total += ct
        if ct > 0:
            if link_type == "issuetype":
                jql_link = build_jql_url(jira_url, filter_id, issuetype=col)
            else:
                jql_link = build_jql_url(jira_url, filter_id, project=col)
            table_html += f'<td><a href="{jql_link}" target="_blank">{ct}</a></td>'
        else:
            table_html += '<td></td>'
    table_html += f'<td class="total-col">{grand_total}</td></tr>\n'

    table_html += '</table>'
    return table_html


def build_pie_data(projects, top_n=10):
    """Prepare Chart.js pie data: TOP N projects + "Other"."""
    sorted_projects = sorted(projects.items(), key=lambda x: x[1], reverse=True)

    labels = []
    values = []
    colors = []

    for i, (proj, count) in enumerate(sorted_projects):
        if i < top_n:
            labels.append(str(proj))
            values.append(count)
            colors.append(PIE_COLORS[i % len(PIE_COLORS)])
        else:
            # Accumulate into "Other"
            if "其他" not in labels:
                labels.append("其他")
                values.append(0)
                colors.append(OTHER_COLOR)
            values[-1] += count

    return labels, values, colors


def build_pie_chart_html(chart_id, title, labels, values, colors):
    """Build HTML for a Chart.js pie chart."""
    if not labels or sum(values) == 0:
        return f'<h2>{html.escape(title)}</h2>\n<p>(无数据)</p>'

    labels_json = json.dumps(labels, ensure_ascii=False)
    values_json = json.dumps(values)
    colors_json = json.dumps(colors)

    return f'''
<h2>{html.escape(title)}</h2>
<div class="chart-container">
  <canvas id="chart-{chart_id}"></canvas>
</div>
<script>
(function() {{
  var ctx = document.getElementById("chart-{chart_id}").getContext("2d");
  new Chart(ctx, {{
    type: "pie",
    data: {{
      labels: {labels_json},
      datasets: [{{
        data: {values_json},
        backgroundColor: {colors_json},
        borderWidth: 1,
        borderColor: "#fff"
      }}]
    }},
    options: {{
      responsive: true,
      maintainAspectRatio: false,
      plugins: {{
        legend: {{
          position: "right",
          labels: {{
            boxWidth: 14,
            padding: 10,
            font: {{ size: 12 }}
          }}
        }},
        tooltip: {{
          callbacks: {{
            label: function(ctx) {{
              var total = ctx.dataset.data.reduce(function(a, b) {{ return a + b; }}, 0);
              var pct = Math.round(ctx.parsed / total * 1000) / 10;
              return ctx.label + ": " + ctx.parsed + " (" + pct + "%)";
            }}
          }}
        }}
      }}
    }}
  }});
}})();
</script>'''


def generate_html(data, jira_url, output_path=None):
    """Generate the full HTML page with 4 visualizations.

    Expected data structure:
    {
        "week_start": "2026-08-04",
        "week_end": "2026-08-11",
        "jira_url": "https://jira.tcl.com",
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
    week_start = data.get("week_start", "N/A")
    week_end = data.get("week_end", "N/A")
    sections = data.get("sections", {})

    html_parts = []

    # HTML head
    html_parts.append(f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>团队周报可视化（{week_start} 至 {week_end}）</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@3.9.1/dist/chart.min.js"></script>
  <style>
    * {{ margin: 0; padding: 0; box-sizing: border-box; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Noto Sans", sans-serif;
      background: #f4f5f7;
      color: #172B4D;
      line-height: 1.5;
    }}
    #content {{
      max-width: 1100px;
      margin: 0 auto;
      padding: 32px 24px;
      background: #fff;
      min-height: 100vh;
    }}
    h1 {{
      font-size: 24px;
      margin-bottom: 24px;
      padding-bottom: 12px;
      border-bottom: 2px solid #0052CC;
      color: #172B4D;
    }}
    h2 {{
      font-size: 18px;
      margin: 28px 0 12px 0;
      color: #0052CC;
    }}

    /* Matrix table styles */
    .matrix-table {{
      border-collapse: collapse;
      width: 100%;
      margin: 12px 0 8px 0;
      font-size: 13px;
    }}
    .matrix-table th, .matrix-table td {{
      border: 1px solid #DFE1E6;
      padding: 6px 10px;
      text-align: center;
      min-width: 60px;
    }}
    .matrix-table th {{
      background: #F4F5F7;
      font-weight: 600;
      color: #42526E;
    }}
    .matrix-table .row-header {{
      text-align: left;
      font-weight: 600;
      background: #F4F5F7;
      color: #172B4D;
      min-width: 100px;
    }}
    .matrix-table .total-col {{
      font-weight: 700;
      background: #EBECF0;
    }}
    .matrix-table .total-row td {{
      font-weight: 700;
      background: #EBECF0;
    }}
    .matrix-table td:first-child {{
      text-align: left;
      white-space: nowrap;
    }}
    .matrix-table a {{
      color: #0052CC;
      text-decoration: none;
      font-weight: 500;
    }}
    .matrix-table a:hover {{
      text-decoration: underline;
      color: #0747A6;
    }}

    /* Chart container */
    .chart-container {{
      width: 100%;
      max-width: 550px;
      height: 400px;
      margin: 12px auto;
    }}

    /* Footer */
    .footer {{
      margin-top: 32px;
      padding-top: 12px;
      border-top: 1px solid #DFE1E6;
      font-size: 12px;
      color: #6B778C;
    }}
  </style>
</head>
<body>
  <div id="content">
    <h1>团队周报可视化（{week_start} 至 {week_end}）</h1>
''')

    # --- Chart 1: 问题解决 matrix table ---
    rd = sections.get("resolved_defects", {})
    html_parts.append(f'<h2>一、{rd.get("title", "问题解决 — 人员 × 问题类型")}</h2>')
    html_parts.append(build_matrix_table(
        rd, "assignee", "issuetype", "问题类型", "经办人",
        jira_url, rd.get("filter_id", 37711), link_type="issuetype"
    ))

    # --- Chart 2: FR 闭环 matrix table ---
    fr = sections.get("fr_closed", {})
    html_parts.append(f'<h2>二、{fr.get("title", "FR 闭环 — 人员 × 项目")}</h2>')
    html_parts.append(build_matrix_table(
        fr, "assignee", "project", "项目", "经办人",
        jira_url, fr.get("filter_id", 38267), link_type="project"
    ))

    # --- Chart 3: 框架分析 pie chart ---
    fw = sections.get("framework_analyzed", {})
    fw_title = fw.get("title", "框架分析 — 项目分布 TOP 10")
    fw_projects = fw.get("projects", {})
    labels, values, colors = build_pie_data(fw_projects, top_n=10)
    html_parts.append(build_pie_chart_html("fw", fw_title, labels, values, colors))

    # --- Chart 4: XTS 分析 pie chart ---
    xts = sections.get("xts_analyzed", {})
    xts_title = xts.get("title", "XTS 分析 — 项目分布 TOP 10")
    xts_projects = xts.get("projects", {})
    labels, values, colors = build_pie_data(xts_projects, top_n=10)
    html_parts.append(build_pie_chart_html("xts", xts_title, labels, values, colors))

    # Footer
    html_parts.append(f'''
    <div class="footer">报告生成时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</div>
  </div>
  <script>
    // Initialize all charts on page load
    window.addEventListener("DOMContentLoaded", function() {{
      // Charts are initialized inline via IIFE blocks above
    }});
  </script>
</body>
</html>''')

    full_html = "\n".join(html_parts)

    if output_path:
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(full_html)
        print(f"可视化报告已保存至: {output_path}")

    return full_html


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Jira Team Weekly Visual Charts Generator")
    parser.add_argument("--data", "-d", required=True, help="JSON data file path")
    parser.add_argument("--output", "-o", default=None, help="Output HTML file path")
    parser.add_argument("--jira-url", default="https://jira.tcl.com", help="Jira base URL for links")
    args = parser.parse_args()

    with open(args.data, "r", encoding="utf-8") as f:
        data = json.load(f)

    if args.output is None:
        week_start = data.get("week_start", datetime.now().strftime("%Y%m%d"))
        args.output = f"./reports/周报_图表_{week_start}.html"

    generate_html(data, args.jira_url, args.output)


if __name__ == "__main__":
    main()
