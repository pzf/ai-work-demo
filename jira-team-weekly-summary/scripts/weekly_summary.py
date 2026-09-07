"""
Jira Team Weekly Summary - Report Generator

This script takes collected data from the AI agent (via MCP Jira tools)
and generates a formatted Markdown weekly report.

Usage:
    python scripts/weekly_summary.py --data <data_json_file> --output <output_path>
"""

import json
import os
import sys
from datetime import datetime


def calc_trend(this_count: int, last_count: int) -> str:
    """计算周环比趋势，仅返回上涨/下降/持平。"""
    if this_count > last_count:
        return "上涨"
    if this_count < last_count:
        return "下降"
    return "持平"


def _normalize_other(key: str) -> str:
    return "其他" if key in {"其他", "OTHER_PROJECTS", "Other", "OTHER"} else key


def top_list_text(items, top_n=5, required=False, field_name="统计项", normalize_other=False):
    """把排序后的列表格式化为 `name(N)、name(N)` 单行文本。"""
    if isinstance(items, dict):
        pairs = items.items()
    else:
        pairs = [(i.get("name"), i.get("count", 0)) for i in items]
    merged = {}
    for key, value in pairs:
        if not key or not value:
            continue
        name = _normalize_other(key) if normalize_other else key
        merged[name] = merged.get(name, 0) + value
    ordered = sorted(merged.items(), key=lambda x: x[1], reverse=True)
    if not ordered:
        if required:
            raise RuntimeError(f"缺少{field_name}数据")
        return "无"
    return "、".join(f"{k}({v})" for k, v in ordered[:top_n])


def project_text(projects: dict, top_n=5, required=False, field_name="项目分布", normalize_other=False):
    """项目分布 dict 格式化为 `PROJ(N)、PROJ(N)` 单行文本。"""
    return top_list_text(projects, top_n, required, field_name, normalize_other)


def section_by_person(section_data: dict) -> dict:
    if "this_week" in section_data:
        by_person = section_data.get("this_week", {}).get("by_person", {})
        if by_person:
            return by_person
    by_person = section_data.get("by_person", {})
    if by_person:
        return by_person
    matrix = section_data.get("matrix", {})
    if matrix:
        return {person: sum(cols.values()) for person, cols in matrix.items()}
    return {}


def section_projects(section_data: dict, matrix_col_type: str = None) -> dict:
    if "this_week" in section_data:
        by_project = section_data.get("this_week", {}).get("by_project", {})
        if by_project:
            return by_project
    projects = section_data.get("projects", section_data.get("by_project", {}))
    if projects:
        return projects
    if section_data.get("matrix_projects"):
        return section_data.get("matrix_projects", {})
    matrix = section_data.get("matrix", {})
    if matrix and (section_data.get("matrix_col_type") == "project" or matrix_col_type == "project"):
        project_counts = {}
        for cols in matrix.values():
            for project, count in cols.items():
                project_counts[project] = project_counts.get(project, 0) + count
        return project_counts
    return {}


def generate_report(data: dict, output_path: str = None) -> str:
    """
    生成周报总结，紧凑纯文字格式（与 SKILL.md Step 4 一致）。

    Expected data structure:
    {
        "week_start": "2026-08-06",
        "week_end": "2026-08-13",
        "team_name": "MyTeam",
        "sections": {
            "resolved_defects": {
                "this_week": {"total": 23, "by_person": {...}, "by_project": {...}},
                "last_week": {"total": 20}
            },
            "fr_closed": {...},
            "framework_analyzed": {
                "this_week": {"total": 74, "by_person": {...}, "by_project": {...}},
                "last_week": {"total": 60}
            },
            "xts_analyzed": {...}
        }
    }
    """
    week_start = data.get("week_start", "N/A")
    week_end = data.get("week_end", "N/A")
    sections = data.get("sections", {})

    lines = [f"## 团队周报分析（{week_start} 至 {week_end}）", ""]

    def section_line(prefix, key, person_field="by_person"):
        sd = sections.get(key, {})
        tw = sd.get("this_week", {})
        lw = sd.get("last_week", {})
        tw_total = tw.get("total", 0)
        lw_total = lw.get("total", 0)
        trend = calc_trend(tw_total, lw_total)
        persons = top_list_text(
            section_by_person(sd), 5,
            required=tw_total > 0, field_name=f"{prefix}贡献排行",
        )
        projects = project_text(
            section_projects(sd, "project" if key == "fr_closed" else None), 5,
            required=tw_total > 0, field_name=f"{prefix}项目分布", normalize_other=True,
        )
        return (
            f"{prefix}本周闭环 {tw_total} 个（上周 {lw_total}，{trend}）。"
            f"贡献排行：{persons}。问题较多的项目：{projects}。"
        )

    lines.append(section_line("一、问题解决", "resolved_defects"))
    lines.append(section_line("二、FR 闭环", "fr_closed"))

    # 框架 / XTS 是"分析"而非"闭环"
    for prefix, key in (("三、框架问题分析", "framework_analyzed"),
                        ("四、XTS 问题分析", "xts_analyzed")):
        sd = sections.get(key, {})
        tw = sd.get("this_week", {})
        lw = sd.get("last_week", {})
        tw_total = tw.get("total", 0)
        lw_total = lw.get("total", 0)
        trend = calc_trend(tw_total, lw_total)
        persons = top_list_text(
            section_by_person(sd), 5,
            required=tw_total > 0, field_name=f"{prefix}贡献排行",
        )
        projects = project_text(
            section_projects(sd), 5,
            required=tw_total > 0, field_name=f"{prefix}项目分布", normalize_other=True,
        )
        lines.append(
            f"{prefix}本周分析 {tw_total} 个（上周 {lw_total}，{trend}）。"
            f"贡献排行：{persons}。问题较多的项目：{projects}。"
        )

    lines.append("")
    lines.append("----")
    lines.append(f"*报告生成时间: {datetime.now().strftime('%Y-%m-%d')}*")

    report = "\n".join(lines)

    if output_path:
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"报告已保存至: {output_path}")

    return report


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Jira Team Weekly Summary Report Generator")
    parser.add_argument("--data", "-d", required=True, help="JSON data file path")
    parser.add_argument("--output", "-o", default=None, help="Output markdown file path")
    args = parser.parse_args()

    with open(args.data, "r", encoding="utf-8") as f:
        data = json.load(f)

    if args.output is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        args.output = f"./reports/weekly_summary_{timestamp}.md"

    report = generate_report(data, args.output)
    print(report)


if __name__ == "__main__":
    main()
