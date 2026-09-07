#!/usr/bin/env python3
"""团队周报数据抓取脚本。

直接通过 connector-proxy 的 MCP 端点调用 Jira 的 jira_search 工具获取数据，
不再依赖手工 JSON 中转文件。产出结构化 JSON，供 weekly_summary.py 和
generate_confluence_static.py 消费。

认证信息从环境变量 CODEBUDDY_MCP_CONFIG 读取（含 connector-proxy 的
Authorization Bearer token 与 X-WorkBuddy-Session-Id）。Filter ID、团队人员
与 Jira URL 从同目录的 config.json 读取。

用法：
    python scripts/fetch_data.py --output data.json
"""
import argparse
import json
import os
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import requests

SKILL_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = SKILL_DIR / "config.json"

FIELDS = "assignee,project,issuetype,status,summary"

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
        self._post({
            "jsonrpc": "2.0", "id": self._next_id, "method": "initialize",
            "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                       "clientInfo": {"name": "weekly-gen", "version": "1.0"}},
        })
        self._next_id += 1
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})

    @staticmethod
    def _parse_sse(resp):
        """从 SSE 响应中提取 data 字段并解析为 JSON。

        connector-proxy 的 SSE 响应 data 字段值内部可能含真实换行符，
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
        # connector-proxy 偶发 "reconnect ripple" 抖动，加入重试
        last_err = None
        for attempt in range(3):
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
            if "reconnect" in text:
                last_err = text[:200]
                time.sleep(2 * (attempt + 1))
                continue
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                raise RuntimeError(f"返回内容无法解析为 JSON：{text[:300]}")
        raise RuntimeError(f"重试后仍失败：{last_err}")

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

    def count(self, jql):
        """只取 total，不拉 issue 详情。"""
        page = self.search(jql, FIELDS, limit=1, start_at=0)
        return page.get("total", 0)


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


def field_key(issue, field):
    value = issue.get(field)
    if isinstance(value, dict):
        return value.get("key") or value.get("name") or "未设置"
    return value or "未设置"


def build_matrix(issues, row_getter, col_getter):
    """构建人员×维度矩阵、by_person、by_project。

    matrix 列 = col_getter 的结果（供二维表）
    by_person = row 合计
    by_project = 按 project.key 统计（供总览"问题较多的项目"）
    """
    matrix = defaultdict(Counter)
    by_person = Counter()
    by_project = Counter()
    for issue in issues:
        row = row_getter(issue)
        col = col_getter(issue)
        matrix[row][col] += 1
        by_person[row] += 1
        by_project[field_key(issue, "project")] += 1
    return dict(matrix), dict(by_person), dict(by_project)


def comment_count_jql(user, window="after -7d"):
    """框架/XTS 个人分析数的 JQL（基于评论行为）。"""
    return f'issueFunction in commented("by {user} {window}") AND issuetype in (Bug, Defect, Defect_new)'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", "-o", required=True, help="输出 JSON 文件路径")
    args = parser.parse_args()

    config = load_config()
    filters_cfg = config["filters"]
    jira_url = config.get("jira_url", "https://jira.tcl.com")
    mcp_url, mcp_headers = load_mcp_auth()
    client = JiraClient(mcp_url, mcp_headers)

    today = datetime.now()
    week_start = (today - timedelta(days=7)).strftime("%Y-%m-%d")
    week_end = today.strftime("%Y-%m-%d")

    data = {
        "week_start": week_start,
        "week_end": week_end,
        "team_name": config.get("team_name", "MyTeam"),
        "jira_url": jira_url,
        "sections": {},
    }

    # 1. 问题解决 & FR 闭环：本周 filter 详情 + 上周 filter 总数
    for key in ("resolved_defects", "fr_closed"):
        fcfg = filters_cfg[key]
        fid = fcfg["filter_id"]
        last_fid = fcfg.get("last_week_filter_id")
        print(f"拉取 {key} (filter={fid})...", file=sys.stderr)
        result = client.search_all(f"filter = {fid}", FIELDS)
        issues = result["issues"]

        # 说明：mcp-jira_jira_search 工具固定不返回 issuetype 字段，
        # 且问题解决/FR filter 内 issue 类型单一（Defect / FR），
        # 因此二维矩阵统一按 project.key 分列，数据真实可得。
        matrix, by_person, by_project = build_matrix(
            issues,
            assignee_username,
            lambda i: field_key(i, "project"),
        )

        section = {
            "filter_id": fid,
            "last_week_filter_id": last_fid,
            "this_week": {
                "total": result["total"],
                "by_person": by_person,
                "by_project": by_project,
            },
            "matrix": matrix,
            "matrix_col_type": "project",
            "projects": by_project,
        }

        if last_fid:
            last_total = client.count(f"filter = {last_fid}")
            section["last_week"] = {"total": last_total}
            print(f"  {key}: 本周 total={result['total']}, 上周 total={last_total}", file=sys.stderr)
        else:
            print(f"  {key}: 本周 total={result['total']}, 无上周 filter", file=sys.stderr)

        data["sections"][key] = section

    # 2. 框架 & XTS 分析：本周 filter total + 项目分布 + 个人评论计数
    for key in ("framework_analyzed", "xts_analyzed"):
        fcfg = filters_cfg[key]
        fid = fcfg["filter_id"]
        members = fcfg.get("team_members", [])
        print(f"拉取 {key} (filter={fid})...", file=sys.stderr)
        result = client.search_all(f"filter = {fid}", FIELDS)
        issues = result["issues"]

        _, _, by_project = build_matrix(
            issues,
            assignee_username,
            lambda i: field_key(i, "project"),
        )

        # 个人分析数：本周（after -7d）
        by_person = {}
        for member in members:
            cnt = client.count(comment_count_jql(member, "after -7d"))
            by_person[member] = cnt
            print(f"  {key} 本周 {member}: {cnt}", file=sys.stderr)

        # 个人分析数：上周（after -14d before -7d），保持现有口径
        last_by_person = {}
        for member in members:
            cnt = client.count(comment_count_jql(member, "after -14d before -7d"))
            last_by_person[member] = cnt
            print(f"  {key} 上周 {member}: {cnt}", file=sys.stderr)

        section = {
            "filter_id": fid,
            "this_week": {
                "total": result["total"],
                "by_person": by_person,
                "by_project": by_project,
            },
            "last_week": {
                "total": sum(last_by_person.values()),
                "by_person": last_by_person,
            },
            "matrix": {},
            "projects": by_project,
        }
        data["sections"][key] = section

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"数据已保存：{out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
