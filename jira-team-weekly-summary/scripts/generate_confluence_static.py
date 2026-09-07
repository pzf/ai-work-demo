"""
Generate static Confluence storage HTML for Jira weekly summary.

No JiraChart macros, no JavaScript, no external resources.
- Output can be a complete 6-column Confluence table matching the weekly page.
- Defect/FR cells keep clickable JQL links.
- Framework/XTS member analysis cells include comment-analysis JQL links.
- Project analysis distribution aggregates Framework + XTS project data with clickable JQL links.
"""

import json
import os
from datetime import datetime
from html import escape
from pathlib import Path
from urllib.parse import quote


TABLE_HEADERS = ["总览", "Defect 修复情况", "FR 闭环情况", "框架分析分布", "XTS分析分布", "项目分析分布"]

SKILL_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = SKILL_DIR / "config.json"


def _default_filter_ids():
    """从 config.json 读取 filter id 作为兜底。"""
    try:
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        return {
            "resolved_defects": cfg["filters"]["resolved_defects"]["filter_id"],
            "fr_closed": cfg["filters"]["fr_closed"]["filter_id"],
            "framework_analyzed": cfg["filters"]["framework_analyzed"]["filter_id"],
            "xts_analyzed": cfg["filters"]["xts_analyzed"]["filter_id"],
        }
    except (OSError, KeyError, json.JSONDecodeError):
        return {}


def build_jql_url(jira_url, filter_id, assignee=None, issue_type=None, project=None, issue_type_jql=None, raw_jql=None):
    if raw_jql:
        return f"{jira_url}/issues/?jql={quote(str(raw_jql), safe='(),=').replace(' ', '%20')}"
    clauses = [f"filter={filter_id}"]
    if assignee:
        clauses.append(f"assignee={quote(str(assignee), safe='')}")
    if issue_type_jql:
        clauses.append(quote(str(issue_type_jql), safe="(),"))
    elif issue_type:
        clauses.append(f"issuetype={quote(str(issue_type), safe='')}")
    if project:
        clauses.append(f"project={quote(str(project), safe='')}")
    return f"{jira_url}/issues/?jql={'%20AND%20'.join(clauses)}"


def link(url, text):
    return f'<a href="{escape(url)}" target="_blank">{escape(str(text))}</a>'


def section_total(section_data):
    if "this_week" in section_data:
        return section_data.get("this_week", {}).get("total", 0)
    if "total" in section_data:
        return section_data.get("total", 0)
    if "projects" in section_data:
        return sum(section_data.get("projects", {}).values())
    if "matrix" in section_data:
        return sum(sum(row.values()) for row in section_data.get("matrix", {}).values())
    return 0


def section_by_person(section_data):
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


def section_projects(section_data, matrix_col_type=None):
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


def top_text(items, limit=6, required=False, field_name="统计项", normalize_other=False):
    if isinstance(items, dict):
        raw_pairs = items.items()
    else:
        raw_pairs = [(i.get("name"), i.get("count", 0)) for i in items]
    merged = {}
    other_keys = {"其他", "OTHER_PROJECTS", "Other", "OTHER"}
    for key, value in raw_pairs:
        if not key or not value:
            continue
        name = "其他" if normalize_other and key in other_keys else key
        merged[name] = merged.get(name, 0) + value
    pairs = sorted(merged.items(), key=lambda x: x[1], reverse=True)
    if not pairs:
        if required:
            raise RuntimeError(f"缺少总览所需的具体{field_name}数据")
        return "无"
    return "、".join(f"{k}({v})" for k, v in pairs[:limit])


def calc_trend(this_count, last_count):
    if this_count > last_count:
        return "上涨"
    if this_count < last_count:
        return "下降"
    return "持平"


def section_last_total(section_data):
    if "last_week" in section_data:
        return section_data.get("last_week", {}).get("total", 0)
    return 0


def build_summary_cell(data):
    sections = data.get("sections", {})
    rd = sections.get("resolved_defects", {})
    fr = sections.get("fr_closed", {})
    fw = sections.get("framework_analyzed", {})
    xts = sections.get("xts_analyzed", {})

    def trend_suffix(section):
        total = section_total(section)
        last = section_last_total(section)
        return f"（上周 {last}，{calc_trend(total, last)}）"

    lines = [
        f"一、问题解决本周闭环 {section_total(rd)} 个{trend_suffix(rd)}。贡献排行：{top_text(section_by_person(rd), required=section_total(rd) > 0, field_name='问题解决贡献排行')}。问题较多的项目：{top_text(section_projects(rd), 5, required=section_total(rd) > 0, field_name='问题解决项目分布', normalize_other=True)}。",
        f"二、FR 闭环本周闭环 {section_total(fr)} 个{trend_suffix(fr)}。贡献排行：{top_text(section_by_person(fr), required=section_total(fr) > 0, field_name='FR 贡献排行')}。问题较多的项目：{top_text(section_projects(fr, 'project'), 5, required=section_total(fr) > 0, field_name='FR 项目分布', normalize_other=True)}。",
        f"三、框架问题分析本周分析 {section_total(fw)} 个{trend_suffix(fw)}。贡献排行：{top_text(section_by_person(fw), required=section_total(fw) > 0, field_name='框架分析贡献排行')}。问题较多的项目：{top_text(section_projects(fw), 5, required=section_total(fw) > 0, field_name='框架项目分布', normalize_other=True)}。",
        f"四、XTS 问题分析本周分析 {section_total(xts)} 个{trend_suffix(xts)}。贡献排行：{top_text(section_by_person(xts), required=section_total(xts) > 0, field_name='XTS 分析贡献排行')}。问题较多的项目：{top_text(section_projects(xts), 5, required=section_total(xts) > 0, field_name='XTS 项目分布', normalize_other=True)}。",
    ]
    spans = [f'<span style="color: rgb(0,51,102);">{escape(line)}</span>' for line in lines]
    return "<p>" + "<br/>".join(spans) + "</p>"


def defect_issue_type_jql():
    return "issuetype in (Defect, Defect_new)"


def build_matrix_table(section_data, row_label, jira_url, filter_id, link_type):
    matrix = section_data.get("matrix", {})
    if not matrix:
        return "<p>无数据</p>"

    cols = sorted(
        {c for row in matrix.values() for c in row.keys()},
        key=lambda c: sum(row.get(c, 0) for row in matrix.values()),
        reverse=True,
    )
    row_totals = {r: sum(v.values()) for r, v in matrix.items()}
    rows = sorted(matrix.keys(), key=lambda r: row_totals[r], reverse=True)
    col_totals = {c: sum(matrix.get(r, {}).get(c, 0) for r in rows) for c in cols}

    html = ['<table class="wrapped relative-table" style="width: 100.0%;font-size: 12.0px;"><tbody><tr>']
    html.append(f"<th>{escape(row_label)}</th>")
    for col in cols:
        html.append(f"<th>{escape(str(col))}</th>")
    html.append("<th>合计</th></tr>")

    for row in rows:
        html.append(f"<tr><td>{escape(str(row))}</td>")
        for col in cols:
            count = matrix[row].get(col, 0)
            if count:
                if link_type == "issue_type":
                    url = build_jql_url(jira_url, filter_id, assignee=row, issue_type_jql=defect_issue_type_jql())
                elif link_type == "issue_type_project":
                    url = build_jql_url(jira_url, filter_id, assignee=row, issue_type_jql=defect_issue_type_jql(), project=col)
                else:
                    url = build_jql_url(jira_url, filter_id, assignee=row, project=col)
                html.append(f"<td>{link(url, count)}</td>")
            else:
                html.append("<td><br/></td>")
        html.append(f"<td>{row_totals[row]}</td></tr>")

    html.append("<tr><th>合计</th>")
    grand_total = 0
    for col in cols:
        ct = col_totals[col]
        grand_total += ct
        if ct:
            if link_type == "issue_type":
                url = build_jql_url(jira_url, filter_id, issue_type_jql=defect_issue_type_jql())
            elif link_type == "issue_type_project":
                url = build_jql_url(jira_url, filter_id, issue_type_jql=defect_issue_type_jql(), project=col)
            else:
                url = build_jql_url(jira_url, filter_id, project=col)
            html.append(f"<th>{link(url, ct)}</th>")
        else:
            html.append("<th><br/></th>")
    html.append(f"<th>{link(build_jql_url(jira_url, filter_id), grand_total)}</th></tr></tbody></table>")
    return "".join(html)


def analysis_member_jql(user):
    return f'issueFunction in commented("by {user} after -7d") AND issuetype in (Bug, Defect, Defect_new)'


def build_member_analysis_table(section_data, jira_url):
    by_person = section_by_person(section_data)
    if not by_person:
        return "<p>无数据</p>"
    rows = sorted(by_person.items(), key=lambda x: x[1], reverse=True)
    total = sum(count for _, count in rows)
    html = ['<table class="wrapped relative-table" style="width: 100.0%;font-size: 12.0px;"><tbody>']
    html.append("<tr><th>团队成员</th><th>分析数量</th><th>占比</th></tr>")
    for user, count in rows:
        pct = (count / total * 100) if total else 0
        url = build_jql_url(jira_url, None, raw_jql=analysis_member_jql(user))
        html.append(f"<tr><td>{escape(str(user))}</td><td>{link(url, count)}</td><td>{pct:.1f}%</td></tr>")
    html.append(f"<tr><th>总数</th><th>{total}</th><th>100.0%</th></tr>")
    html.append("</tbody></table>")
    return "".join(html)


def merge_analysis_distribution(*sections):
    projects = {}
    total = 0
    for section in sections:
        total += section_total(section)
        for project, count in section_projects(section).items():
            normalized = "其他" if project in {"其他", "OTHER_PROJECTS", "Other", "OTHER"} else project
            projects[normalized] = projects.get(normalized, 0) + count
    return {"total": total, "projects": projects}


def combined_analysis_jql(filter_ids, project=None):
    filters = [str(f) for f in filter_ids if f]
    if not filters:
        return None
    base = "(" + " OR ".join(f"filter={f}" for f in filters) + ")"
    if project:
        return f"{base} AND project={project}"
    return base


def build_project_distribution_table(section_data, jira_url, filter_id=None, filter_ids=None):
    projects = section_projects(section_data)
    if not projects:
        return "<p>无数据</p>"

    other_keys = {"其他", "OTHER_PROJECTS", "Other", "OTHER"}
    explicit_other = sum(v for k, v in projects.items() if k in other_keys)
    project_items = [(k, v) for k, v in projects.items() if k not in other_keys]
    sorted_items = sorted(project_items, key=lambda x: x[1], reverse=True)
    total = section_total(section_data) or (sum(v for _, v in project_items) + explicit_other)
    known_total = sum(v for _, v in project_items) + explicit_other
    top_items = sorted_items[:10]
    other = explicit_other + sum(v for _, v in sorted_items[10:])
    if total > known_total:
        other += total - known_total
    display_items = top_items + ([('其他', other)] if other else [])

    html = ['<table class="wrapped relative-table" style="width: 100.0%;font-size: 12.0px;"><tbody>']
    html.append("<tr><th>项目</th><th>数量</th><th>占比</th></tr>")
    for project, count in display_items:
        pct = (count / total * 100) if total else 0
        if project == "其他":
            project_text = "其他"
            count_text = escape(str(count))
        elif filter_id:
            project_url = build_jql_url(jira_url, filter_id, project=project)
            project_text = link(project_url, project)
            count_text = link(project_url, count)
        elif filter_ids:
            project_jql = combined_analysis_jql(filter_ids, project=project)
            project_url = build_jql_url(jira_url, None, raw_jql=project_jql)
            project_text = link(project_url, project)
            count_text = link(project_url, count)
        else:
            project_text = escape(str(project))
            count_text = escape(str(count))
        html.append(f"<tr><td>{project_text}</td><td>{count_text}</td><td>{pct:.1f}%</td></tr>")
    if filter_id:
        total_text = link(build_jql_url(jira_url, filter_id), total)
    elif filter_ids:
        total_text = link(build_jql_url(jira_url, None, raw_jql=combined_analysis_jql(filter_ids)), total)
    else:
        total_text = escape(str(total))
    html.append(f"<tr><th>总数</th><th>{total_text}</th><th>100.0%</th></tr>")
    html.append("</tbody></table>")
    return "".join(html)


def generate_complete_confluence_table(data, jira_url):
    sections = data.get("sections", {})
    rd = sections.get("resolved_defects", {})
    fr = sections.get("fr_closed", {})
    fw = sections.get("framework_analyzed", {})
    xts = sections.get("xts_analyzed", {})

    # filter id 优先取 section 里的，缺失时从 config.json 兜底
    defaults = _default_filter_ids()
    rd_filter = rd.get("filter_id") or defaults.get("resolved_defects")
    fr_filter = fr.get("filter_id") or defaults.get("fr_closed")
    fw_filter = fw.get("filter_id") or defaults.get("framework_analyzed")
    xts_filter = xts.get("filter_id") or defaults.get("xts_analyzed")

    overall_analysis = merge_analysis_distribution(fw, xts)
    cells = [
        build_summary_cell(data),
        build_matrix_table(rd, "经办人", jira_url, rd_filter, "issue_type_project"),
        build_matrix_table(fr, "经办人", jira_url, fr_filter, "project"),
        build_member_analysis_table(fw, jira_url),
        build_member_analysis_table(xts, jira_url),
        build_project_distribution_table(overall_analysis, jira_url, filter_ids=[fw_filter, xts_filter]),
    ]

    html = [
        '<table class="relative-table wrapped" style="width: 100.0%;">',
        '<colgroup><col style="width: 20.0%;"/><col style="width: 16.0%;"/><col style="width: 16.0%;"/><col style="width: 16.0%;"/><col style="width: 16.0%;"/><col style="width: 16.0%;"/></colgroup>',
        '<tbody><tr>',
    ]
    for header in TABLE_HEADERS:
        html.append(f"<th>{escape(header)}</th>")
    html.append("</tr><tr>")
    for cell in cells:
        html.append(f"<td>{cell}</td>")
    html.append("</tr></tbody></table>")
    return "".join(html)


def validate_summary_content(content):
    forbidden = [
        "贡献排行：。",
        "问题较多的项目：。",
        "贡献排行：</span>",
        "问题较多的项目：</span>",
    ]
    for text in forbidden:
        if text in content:
            raise RuntimeError(f"summary content has empty field: {text}")


def generate_confluence_static(data, jira_url):
    content = generate_complete_confluence_table(data, jira_url)
    validate_summary_content(content)
    return content


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", "-d", required=True)
    parser.add_argument("--output", "-o", required=True)
    parser.add_argument("--jira-url", default="https://jira.tcl.com")
    args = parser.parse_args()

    with open(args.data, "r", encoding="utf-8") as f:
        data = json.load(f)
    content = generate_confluence_static(data, args.jira_url)
    if 'jirachart' in content:
        raise RuntimeError("generated content must not contain JiraChart macro")
    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        f.write(content)
    print(args.output)


if __name__ == "__main__":
    main()
