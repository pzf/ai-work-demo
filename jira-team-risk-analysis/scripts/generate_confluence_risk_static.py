#!/usr/bin/env python3
"""生成团队风险分析 Confluence 静态表格。

直接通过 connector-proxy 的 MCP 端点调用 Jira 的 jira_search 工具获取数据，
不再依赖手工 JSON 中转文件。

认证信息从环境变量 CODEBUDDY_MCP_CONFIG 读取（含 connector-proxy 的
Authorization Bearer token 与 X-WorkBuddy-Session-Id）。Filter ID 与 Jira URL
从同目录的 config.json 读取。

用法：
    python generate_confluence_risk_static.py --output 风险分析_静态图表_YYYY-MM-DD.html
"""
import argparse
import html
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import quote

import requests

SKILL_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = SKILL_DIR / "config.json"

ASSIGNEE_ALIASES = {
    "zihang.gao": ["zihang.gao", "Zihang Gao 高梓航"],
    "yihuachen": ["yihuachen", "Yihua Chen陈艺华"],
    "siyu.zhang": ["siyu.zhang", "Siyu Zhang 张思宇"],
    "chuntian.ben": ["chuntian.ben", "贲春田(chuntian.Ben)"],
    "hailongwang": ["hailongwang", "王海龙(HaiLong.Wang)"],
    "ex_jiawei.liu": ["ex_jiawei.liu", "Jiawei Liu 刘嘉伟"],
    "zhanfengpeng": ["zhanfengpeng", "Zhanfeng Peng 彭展峰"],
    "yi-chen": ["yi-chen", "Yi Chen 陈益"],
    "forong.li": ["forong.li", "Forong li 李佛榕"],
    "zhongwen.nong": ["zhongwen.nong", "Zhongwen Nong 农忠文"],
}


def load_config():
    if not CONFIG_PATH.exists():
        sys.exit(f"找不到配置文件：{CONFIG_PATH}")
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def load_mcp_auth():
    """从 CODEBUDDY_MCP_CONFIG 提取 connector-proxy 的 URL 与认证头。"""
    raw = os.environ.get("CODEBUDDY_MCP_CONFIG", "")
    if not raw:
        sys.exit("缺少环境变量 CODEBUDDY_MCP_CONFIG，无法获取 Jira 访问凭证")
    try:
        cfg = json.loads(raw)
    except json.JSONDecodeError as e:
        sys.exit(f"CODEBUDDY_MCP_CONFIG 解析失败：{e}")
    cp = cfg.get("mcpServers", {}).get("connector-proxy", {})
    url = cp.get("url", "http://127.0.0.1:56677/mcp")
    headers = cp.get("headers", {})
    return url, headers


class JiraClient:
    """通过 connector-proxy MCP 端点调用 jira_search。"""

    TOOL_NAME = "mcp-jira_jira_search"

    def __init__(self, mcp_url, headers):
        self.mcp_url = mcp_url
        self.headers = dict(headers)
        self.session = requests.Session()
        self.session_id = None
        self._next_id = 1
        self._initialize()

    def _post(self, payload, with_session=True):
        h = dict(self.headers)
        if with_session and self.session_id:
            h["Mcp-Session-Id"] = self.session_id
        h.setdefault("Content-Type", "application/json")
        h.setdefault("Accept", "application/json, text/event-stream")
        resp = self.session.post(self.mcp_url, headers=h, json=payload, timeout=120)
        resp.raise_for_status()
        if with_session and not self.session_id:
            sid = resp.headers.get("Mcp-Session-Id")
            if sid:
                self.session_id = sid
        return resp

    def _initialize(self):
        r = self._post({
            "jsonrpc": "2.0", "id": self._next_id, "method": "initialize",
            "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                       "clientInfo": {"name": "risk-gen", "version": "1.0"}},
        })
        self._next_id += 1
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})

    @staticmethod
    def _parse_sse(resp):
        """从 SSE 响应中提取 data 字段并解析为 JSON。

        connector-proxy 的 SSE 响应形如：
            event: message
            data: {"result": ...}
        其中 data 字段的值（JSON 字符串）内部可能含真实换行符，
        因此不能按行 split 提取，而应截取 `data: ` 之后到结尾的完整内容。
        """
        text = resp.text
        if text.startswith("event:"):
            text = text.split("\n", 1)[1]
        if text.startswith("data:"):
            text = text[5:].lstrip()
        text = text.strip()
        if not text:
            raise RuntimeError(f"无有效 data 字段：{resp.text[:500]}")
        return json.loads(text)

    def _call(self, tool, arguments):
        r = self._post({
            "jsonrpc": "2.0", "id": self._next_id, "method": "tools/call",
            "params": {"name": tool, "arguments": arguments},
        })
        self._next_id += 1
        result = self._parse_sse(r)
        if result.get("error"):
            raise RuntimeError(f"MCP 错误：{result['error']}")
        content = result.get("result", {}).get("content", [])
        if not content:
            raise RuntimeError("MCP 返回空 content")
        text = content[0].get("text", "")
        if not text:
            raise RuntimeError("MCP 返回空文本")
        if content[0].get("isError"):
            raise RuntimeError(f"工具调用失败：{text}")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # 文本可能直接是错误提示
            raise RuntimeError(f"返回内容无法解析为 JSON：{text[:300]}")

    def search(self, jql, fields, limit=100, start_at=0):
        return self._call(self.TOOL_NAME, {
            "jql": jql, "fields": fields, "limit": limit, "start_at": start_at,
        })

    def search_all(self, jql, fields, page_size=100):
        """分页拉取全部 issue，返回合并后的 issues 列表与 total。"""
        all_issues = []
        total = None
        start_at = 0
        while True:
            page = self.search(jql, fields, limit=page_size, start_at=start_at)
            if total is None:
                total = page.get("total", 0)
            issues = page.get("issues", [])
            all_issues.extend(issues)
            start_at += len(issues)
            if not issues or start_at >= total:
                break
        return {"total": total, "issues": all_issues}


def assignee_username(issue):
    assignee = issue.get("assignee") or {}
    email = assignee.get("email") or ""
    if email.endswith("@tcl.com"):
        return email[:-8]
    name = assignee.get("name") or assignee.get("display_name") or "未分配"
    for username, aliases in ASSIGNEE_ALIASES.items():
        if name in aliases:
            return username
    return name


def field_name(issue, field):
    value = issue.get(field)
    if isinstance(value, dict):
        return value.get("key") or value.get("name") or "未设置"
    return value or "未设置"


def result_name(issue):
    resolution = issue.get("resolution")
    if isinstance(resolution, dict) and resolution.get("name"):
        return "resolution", resolution["name"]
    status = issue.get("status") or {}
    return "status", status.get("name") or "未设置"


def jql_url(jira_url, filter_id, assignee=None, result=None, project=None):
    clauses = [f"filter={filter_id}"]
    if assignee:
        clauses.append(f"assignee={quote(str(assignee), safe='')}")
    if result:
        field, value = result
        clauses.append(f"{field}={quote(str(value), safe='')}")
    if project:
        clauses.append(f"project={quote(str(project), safe='')}")
    return f"{jira_url.rstrip('/')}/issues/?jql={'%20AND%20'.join(clauses)}"


def link(url, text):
    return f'<a href="{html.escape(url, quote=True)}" target="_blank">{html.escape(str(text))}</a>'


def counter_matrix(issues, col_getter):
    rows = defaultdict(Counter)
    cols = Counter()
    labels = {}
    for issue in issues:
        row = assignee_username(issue)
        col = col_getter(issue)
        key = col if not isinstance(col, tuple) else col
        label = col[1] if isinstance(col, tuple) else col
        rows[row][key] += 1
        cols[key] += 1
        labels[key] = label
    row_order = sorted(rows, key=lambda r: sum(rows[r].values()), reverse=True)
    col_order = [c for c, _ in cols.most_common()]
    return rows, row_order, col_order, labels


def build_matrix_table(title, issues, jira_url, filter_id, col_getter, link_mode):
    rows, row_order, col_order, labels = counter_matrix(issues, col_getter)
    total = len(issues)
    parts = []
    if title:
        parts.append(f'<h4>{html.escape(title)}</h4>')
    parts.append('<table class="wrapped relative-table" style="width: 100.0%;font-size: 12.0px;">')
    parts.append('<tbody>')
    parts.append('<tr><th>经办人</th>')
    for col in col_order:
        parts.append(f'<th>{html.escape(str(labels[col]))}</th>')
    parts.append('<th>合计</th></tr>')
    for row in row_order:
        row_total = sum(rows[row].values())
        parts.append(f'<tr><td>{html.escape(row)}</td>')
        for col in col_order:
            count = rows[row].get(col, 0)
            if count:
                if link_mode == "result":
                    url = jql_url(jira_url, filter_id, assignee=row, result=col)
                else:
                    url = jql_url(jira_url, filter_id, assignee=row, project=col)
                parts.append(f'<td>{link(url, count)}</td>')
            else:
                parts.append('<td><br/></td>')
        parts.append(f'<td>{link(jql_url(jira_url, filter_id, assignee=row), row_total)}</td></tr>')
    parts.append('<tr><th>合计</th>')
    for col in col_order:
        col_total = sum(rows[row].get(col, 0) for row in row_order)
        if link_mode == "result":
            url = jql_url(jira_url, filter_id, result=col)
        else:
            url = jql_url(jira_url, filter_id, project=col)
        parts.append(f'<th>{link(url, col_total)}</th>')
    parts.append(f'<th>{link(jql_url(jira_url, filter_id), total)}</th></tr>')
    parts.append('</tbody></table>')
    return ''.join(parts)


def build_project_distribution_table(issues, jira_url, filter_id):
    projects = Counter(field_name(issue, "project") for issue in issues)
    total = len(issues)
    if not projects:
        return '<p>无数据</p>'

    parts = ['<table class="wrapped relative-table" style="width: 100.0%;font-size: 12.0px;">']
    parts.append('<tbody>')
    parts.append('<tr><th>项目</th><th>数量</th><th>占比</th></tr>')
    for project, count in projects.most_common():
        percent = count / total * 100 if total else 0
        url = jql_url(jira_url, filter_id, project=project)
        parts.append(
            f'<tr><td>{link(url, project)}</td><td>{link(url, count)}</td><td>{percent:.1f}%</td></tr>'
        )
    parts.append(f'<tr><th>总数</th><th>{link(jql_url(jira_url, filter_id), total)}</th><th>100.0%</th></tr>')
    parts.append('</tbody></table>')
    return ''.join(parts)


def risk_summary(data):
    pending = data["filters"]["pending_issues"]
    blocked = data["filters"]["blocked_issues"]
    fr = data["filters"]["pending_fr"]
    pending_counts = Counter(assignee_username(i) for i in pending["issues"])
    blocked_counts = Counter(assignee_username(i) for i in blocked["issues"])
    fr_counts = Counter(assignee_username(i) for i in fr["issues"])

    def fmt(counter):
        return '、'.join(f'{u}({n})' for u, n in counter.most_common()) or '无'

    return (
        f'<p><span style="color: rgb(0,51,102);">一、待解决问题 {pending.get("total", len(pending["issues"]))} 个。人员分布：{html.escape(fmt(pending_counts))}。</span><br/>'
        f'<span style="color: rgb(0,51,102);">二、Block 问题 {blocked.get("total", len(blocked["issues"]))} 个。人员分布：{html.escape(fmt(blocked_counts))}。</span><br/>'
        f'<span style="color: rgb(0,51,102);">三、待解决 FR {fr.get("total", len(fr["issues"]))} 个。人员分布：{html.escape(fmt(fr_counts))}。</span></p>'
    )


def build_confluence_table(data, jira_url):
    filters = data["filters"]
    f1 = filters["pending_issues"]
    f2 = filters["blocked_issues"]
    f3 = filters["pending_fr"]
    table1 = build_matrix_table("", f1["issues"], jira_url, f1["filter_id"], result_name, "result")
    table2 = build_matrix_table("", f2["issues"], jira_url, f2["filter_id"], lambda i: field_name(i, "project"), "project")
    table3 = build_project_distribution_table(f1["issues"], jira_url, f1["filter_id"])
    table4 = build_matrix_table("", f3["issues"], jira_url, f3["filter_id"], result_name, "result")
    table5 = build_project_distribution_table(f3["issues"], jira_url, f3["filter_id"])
    return ''.join([
        '<table class="relative-table wrapped" style="width: 100.0%;">',
        '<colgroup><col style="width: 18.0%;"/><col style="width: 17.0%;"/><col style="width: 16.0%;"/><col style="width: 17.0%;"/><col style="width: 16.0%;"/><col style="width: 16.0%;"/></colgroup>',
        '<tbody>',
        '<tr><th>总览</th><th>Defect 分布情况</th><th>Block 分布情况</th><th>Defect 项目分布情况</th><th>FR 分布情况</th><th>FR 项目分布情况</th></tr>',
        '<tr><td>', risk_summary(data), '</td><td>', table1, '</td><td>', table2, '</td><td>', table3, '</td><td>', table4, '</td><td>', table5, '</td></tr>',
        '</tbody></table>'
    ])


FIELDS = "summary,assignee,status,project,issuetype,priority,resolution"


def validate_no_empty(data):
    """发布前质量检查：总览列人员分布不得为空。"""
    for key, label in [("pending_issues", "待解决"), ("blocked_issues", "Block"), ("pending_fr", "FR")]:
        f = data["filters"][key]
        total = f.get("total", len(f["issues"]))
        if total > 0 and not f["issues"]:
            sys.exit(f"质量检查失败：{label} 问题总数 {total} > 0，但 issue 列表为空")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    args = parser.parse_args()

    config = load_config()
    filters_cfg = config["filters"]
    jira_url = config.get("jira_url", "https://jira.tcl.com")
    mcp_url, mcp_headers = load_mcp_auth()

    client = JiraClient(mcp_url, mcp_headers)

    print("正在拉取三个 filter 数据...", file=sys.stderr)
    data = {"filters": {}}
    for key in ("pending_issues", "blocked_issues", "pending_fr"):
        fcfg = filters_cfg[key]
        fid = fcfg["filter_id"]
        if fid is None:
            sys.exit(f"filter_id 未配置：{key}")
        jql = f"filter = {fid}"
        result = client.search_all(jql, FIELDS)
        data["filters"][key] = {
            "filter_id": fid,
            "total": result["total"],
            "issues": result["issues"],
        }
        print(f"  {key}: total={result['total']}, fetched={len(result['issues'])}", file=sys.stderr)

    validate_no_empty(data)

    content = build_confluence_table(data, jira_url)
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(content, encoding="utf-8")
    print(f"已生成：{out_path}", file=sys.stderr)


if __name__ == '__main__':
    main()
