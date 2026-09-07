#!/usr/bin/env python3
"""
Jira FR/Defect Task Assignment - 根据标题或模块匹配自动分配 FR/Defect 任务

工作流程：
1. 从 ~/.gerrit_env 加载 Jira 配置
2. 从 config.json 加载分配规则
3. JQL 查询指定 Owner 名下未解决的 FR/Defect 任务
4. 遍历每个任务，根据规则匹配并确定 assignee
5. 预览模式：输出分配报告，征得用户同意后执行
6. 输出分配详情报告

环境变量（~/.gerrit_env）：
  JIRA_URL       - Jira 服务器基础 URL
  JIRA_USER      - Jira 用户名
  JIRA_PASS      - Jira 密码或 API Token

用法：
  python3 jira_fr_assign.py --owner zhangsan
  python3 jira_fr_assign.py --owner zhangsan --dry-run
"""

import argparse
import base64
import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_JIRA_URL = "https://jira.tcl.com"

# Skill 目录下的 config.json 路径
_SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
_CONFIG_PATH = os.path.join(os.path.dirname(_SKILL_DIR), "config.json")


def _ssl_context():
    """SSL context for Jira API (legacy TLS renegotiation support)."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    ctx.options |= 0x4  # OP_LEGACY_SERVER_CONNECT
    return ctx


def load_gerrit_env():
    """从 ~/.gerrit_env 加载配置到环境变量（如果尚未设置）。"""
    env_path = os.path.expanduser("~/.gerrit_env")
    if not os.path.isfile(env_path):
        return
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, val = line.split("=", 1)
                key = key.strip()
                val = val.strip()
                if key and val and os.environ.get(key) is None:
                    os.environ[key] = val


def _gerrit_env_loaded():
    static_loaded = getattr(_gerrit_env_loaded, "_loaded", False)
    if not static_loaded:
        load_gerrit_env()
        _gerrit_env_loaded._loaded = True


def load_assign_config():
    """加载分配规则配置（title_rules, module_rules）。"""
    if os.path.isfile(_CONFIG_PATH):
        try:
            with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            print(f"ERROR: config.json 解析失败 ({e})", file=sys.stderr)
            sys.exit(1)
    return {}


def get_jira_config(key, default=None):
    """获取 Jira 配置值，从 ~/.gerrit_env 加载。"""
    env_path = os.path.expanduser("~/.gerrit_env")
    if os.path.isfile(env_path):
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if "=" in line:
                        k, v = line.split("=", 1)
                        k = k.strip()
                        v = v.strip()
                        if k == key and v:
                            return v
        except Exception:
            pass
    return default


# ---------------------------------------------------------------------------
# 认证 & HTTP 请求
# ---------------------------------------------------------------------------

def _get_auth_header():
    """构建认证头。优先使用 Bearer Token，其次使用 Basic Auth。"""
    user = get_jira_config("JIRA_USER")
    password = get_jira_config("JIRA_PASS")

    if not user or not password:
        print("ERROR: JIRA_USER 和/或 JIRA_PASS 未设置（~/.gerrit_env）。", file=sys.stderr)
        print("  请设置：", file=sys.stderr)
        print("    export JIRA_USER='your_username'", file=sys.stderr)
        print("    export JIRA_PASS='your_password_or_api_token'", file=sys.stderr)
        sys.exit(1)

    # 如果密码看起来像 API token（非纯数字密码），尝试 Bearer
    if not password.isdigit():
        return f"Bearer {password}"

    # 回退到 Basic Auth
    auth = f"{user}:{password}"
    encoded = base64.b64encode(auth.encode("utf-8")).decode("ascii")
    return f"Basic {encoded}"


def jira_request(endpoint, method="GET", data=None):
    """发送 HTTP 请求到 Jira REST API，返回解析后的 JSON。"""
    base_url = get_jira_config("JIRA_URL", DEFAULT_JIRA_URL)
    url = f"{base_url}/rest/api/2/{endpoint}"

    headers = {
        "Authorization": _get_auth_header(),
        "Content-Type": "application/json; charset=UTF-8",
    }

    body = None
    if data is not None:
        body = json.dumps(data).encode("utf-8")

    req = urllib.request.Request(url, data=body, headers=headers, method=method)

    try:
        with urllib.request.urlopen(req, context=_ssl_context()) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        print(f"ERROR: HTTP {e.code} — {e.reason}", file=sys.stderr)
        print(f"  URL: {url}", file=sys.stderr)
        print(f"  响应: {err_body}", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"ERROR: 连接失败 — {e.reason}", file=sys.stderr)
        sys.exit(1)


def jira_request_put(endpoint, data=None):
    """发送 PUT 请求到 Jira REST API，Jira 的 PUT 更新返回空响应。"""
    base_url = get_jira_config("JIRA_URL", DEFAULT_JIRA_URL)
    url = f"{base_url}/rest/api/2/{endpoint}"

    headers = {
        "Authorization": _get_auth_header(),
        "Content-Type": "application/json; charset=UTF-8",
    }

    body = None
    if data is not None:
        body = json.dumps(data).encode("utf-8")

    req = urllib.request.Request(url, data=body, headers=headers, method="PUT")

    try:
        with urllib.request.urlopen(req, context=_ssl_context()) as resp:
            resp_body = resp.read().decode("utf-8")
            if resp_body:
                return json.loads(resp_body)
            return {}
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        print(f"ERROR: HTTP {e.code} — {e.reason}", file=sys.stderr)
        print(f"  URL: {url}", file=sys.stderr)
        print(f"  响应: {err_body}", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"ERROR: 连接失败 — {e.reason}", file=sys.stderr)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Jira API 操作
# ---------------------------------------------------------------------------

def search_fr_tasks(owner, issue_types=None, max_results=500):
    """搜索指定 Owner 名下未解决的 FR/Defect 任务。"""
    if issue_types is None:
        issue_types = ["FR", "Defect"]
    type_list = ", ".join([f'"{t}"' for t in issue_types])
    jql = (
        f'issuetype in ({type_list}) '
        f'AND assignee = "{owner}" '
        f'AND resolution in (Unresolved, Reopen) '
        f'AND status != Accepted'
    )
    params = urllib.parse.urlencode({
        "jql": jql,
        "maxResults": max_results,
        "fields": "key,summary,components,assignee,status,issuelinks"
    })
    result = jira_request(f"search?{params}")
    return result.get("issues", [])


def get_user_display_name(owner):
    """获取用户的 display name（用于 assignee 字段）。"""
    try:
        user = jira_request(f"user?username={owner}")
        return user.get("displayName", owner)
    except Exception:
        return owner


def update_assignee(issue_key, owner):
    """更新 Issue 的 assignee。"""
    # 获取用户信息
    user = jira_request(f"user?username={owner}")
    # Jira Server: 使用 name 字段 (username)
    user_name = user.get("name")
    if not user_name:
        print(f"ERROR: 无法获取用户 {owner} 的 name", file=sys.stderr)
        return False

    # Jira Server: assignee 使用 name 字段
    payload = {
        "fields": {
            "assignee": {"name": user_name}
        }
    }

    try:
        jira_request_put(f"issue/{issue_key}", data=payload)
        return True
    except Exception as e:
        print(f"ERROR: 更新 {issue_key} 失败: {e}", file=sys.stderr)
        return False


# ---------------------------------------------------------------------------
# 匹配逻辑
# ---------------------------------------------------------------------------

def _match_patterns(text, rules):
    """用规则列表匹配文本（兼容普通子串与合并后的正则）。

    每个 rule 可含：
      - `pattern`: 普通子串（大小写不敏感）
      - `patterns`: 该 owner 的多个 pattern 列表，内部用正则 OR（|）匹配
      - `regex`: 预编译正则（等价于 patterns 的 OR）

    返回命中的 owner 或 None。按规则列表顺序取第一个命中（保持精确者优先）。
    """
    if not text:
        return None
    text_lower = text.lower()
    for rule in rules:
        # 合并正则（预编译缓存）
        regex = rule.get("_regex")
        if regex is None:
            patterns = rule.get("patterns") or ([rule["pattern"]] if rule.get("pattern") else None)
            if patterns:
                regex = re.compile("|".join(re.escape(p.lower()) for p in patterns))
            else:
                regex = None
            rule["_regex"] = regex
        if regex and regex.search(text_lower):
            return rule.get("owner")
    return None


def match_by_title(summary, title_rules):
    """根据标题模糊匹配（substring，大小写不敏感；支持合并正则）。"""
    if not summary or not title_rules:
        return None
    return _match_patterns(summary, title_rules)


def match_by_component(components, module_rules):
    """根据组件精确匹配。"""
    if not components or not module_rules:
        return None

    # components 是数组，每个 component 有 name 字段
    component_names = [c.get("name", "").lower() for c in components]
    for rule in module_rules:
        pattern = rule.get("pattern", "").lower()
        if pattern in component_names:
            return rule.get("owner")
    return None


def _link_side(link, side):
    """取链接某一侧（outward/inward）的 issue dict，兼容 camelCase 与 snake_case。"""
    for k in (f"{side}Issue", f"{side}_issue"):
        v = link.get(k)
        if v:
            return v
    return None


def extract_clone_sources(issuelinks):
    """从 issuelinks 中提取 Cloners 链接的来源需求 FR key。

    实测确认：来源需求 FR 位于 Cloners 链接的 outward 侧
    （inward 侧在样本中均为空）。防御性同时检查 inward，
    但 outward 优先。

    返回: 来源 FR key 列表（按出现顺序，去重）。
    """
    sources = []
    if not issuelinks:
        return sources
    seen = set()
    for link in issuelinks:
        link_type = link.get("type", {})
        if link_type.get("name") != "Cloners":
            continue
        # 优先 outward，其次 inward
        src_key = None
        outward = _link_side(link, "outward")
        if outward:
            src_key = outward.get("key")
        if not src_key:
            inward = _link_side(link, "inward")
            if inward:
                src_key = inward.get("key")
        if src_key and src_key not in seen:
            seen.add(src_key)
            sources.append(src_key)
    return sources


def _rule_source_keys(rule):
    """从规则中提取全部 source_key（支持单个 source_key 或合并的 source_keys 数组）。"""
    keys = []
    sk = rule.get("source_key")
    if sk:
        keys.append(sk)
    for k in rule.get("source_keys") or []:
        if k:
            keys.append(k)
    return keys


def match_by_clone_source(issuelinks, clone_source_rules):
    """根据克隆来源匹配 owner。

    规则按来源需求库项目分组（clone_source_rules 的顶层 key = 项目前缀）。
    从来源 FR key 解析项目前缀，定位对应分组；组内先按 source_key/source_keys
    精确匹配，再按 pattern/patterns 标题子串匹配（大小写不敏感）。

    返回: (owner, source_key) 或 (None, None)。
    """
    if not issuelinks or not clone_source_rules:
        return None, None

    sources = extract_clone_sources(issuelinks)
    for src_key in sources:
        # 解析项目前缀：key 中 '-' 前的部分（如 GOOGLEGMS-139 -> GOOGLEGMS）
        project = src_key.split("-", 1)[0] if "-" in src_key else src_key
        rules = clone_source_rules.get(project)
        if not rules:
            continue

        # 先按 source_key/source_keys 精确匹配
        for rule in rules:
            if src_key in _rule_source_keys(rule):
                return rule.get("owner"), src_key

        # 再按 pattern/patterns 标题匹配（不区分大小写，支持合并正则）
        # 说明：pattern 匹配需要来源 FR 标题，这里 issuelinks 中已带 summary。
        src_summary = _source_summary_by_key(issuelinks, src_key)
        if not src_summary:
            continue
        owner = _match_patterns(src_summary, rules)
        if owner:
            return owner, src_key

    return None, None


def _source_summary_by_key(issuelinks, src_key):
    """根据来源 FR key 从 issuelinks 中反查其标题 summary。"""
    if not issuelinks:
        return None
    for link in issuelinks:
        for side in ("outward", "inward"):
            side_issue = _link_side(link, side)
            if side_issue and side_issue.get("key") == src_key:
                return side_issue.get("fields", {}).get("summary", "")
    return None


def determine_assignment(issue, title_rules, module_rules, clone_source_rules):
    """确定 FR 任务的 assignee。返回 (new_owner, match_type, original_owner, source_key)。"""
    fields = issue.get("fields", {})
    summary = fields.get("summary", "")
    components = fields.get("components", [])
    issuelinks = fields.get("issuelinks", [])
    original_owner = fields.get("assignee", {}).get("displayName", "")

    # 优先级：克隆来源匹配 > 标题匹配 > 模块匹配 > 保持不变
    new_owner, source_key = match_by_clone_source(issuelinks, clone_source_rules)
    if new_owner:
        return new_owner, "clone", original_owner, source_key

    new_owner = match_by_title(summary, title_rules)
    if new_owner:
        return new_owner, "title", original_owner, None

    new_owner = match_by_component(components, module_rules)
    if new_owner:
        return new_owner, "component", original_owner, None

    return None, "none", original_owner, None


# ---------------------------------------------------------------------------
# 输出报告
# ---------------------------------------------------------------------------

def format_report(issues, assignments, owner, dry_run=False):
    """格式化分配报告。"""
    mode_label = "Preview" if dry_run else "Execution"
    prefix = "FR/Defect Task Assignment Report (Preview)" if dry_run else "FR/Defect Task Assignment Report"

    lines = []
    lines.append("=" * 60)
    lines.append(f"{prefix}")
    lines.append("=" * 60)
    lines.append(f"Owner: {owner}")
    lines.append("")
    lines.append(f"JQL: issuetype in (FR, Defect) AND assignee = \"{owner}\"")
    lines.append(f"      AND resolution in (Unresolved, Reopen) AND status != Accepted")
    lines.append("")
    lines.append("-" * 60)

    total = len(issues)
    will_change = sum(1 for a in assignments if a["new_owner"] and a["match_type"] != "none")
    unchanged = total - will_change

    lines.append(f"Total FR/Defect found: {total}")
    lines.append(f"{'Will assign' if dry_run else 'Assigned'}: {will_change}")
    lines.append(f"Unchanged: {unchanged}")
    lines.append("")

    if dry_run:
        lines.append("[Preview Mode - No changes will be made]")
        lines.append("")

    lines.append("-" * 60)
    lines.append("Assignments:")
    lines.append("-" * 60)

    for i, (issue, assignment) in enumerate(zip(issues, assignments), 1):
        key = issue.get("key")
        summary = issue.get("fields", {}).get("summary", "")
        components = issue.get("fields", {}).get("components", [])
        component_names = ", ".join([c.get("name", "") for c in components]) or "None"

        new_owner = assignment["new_owner"]
        match_type = assignment["match_type"]
        original_owner = assignment["original_owner"]
        source_key = assignment.get("source_key")

        lines.append(f"{i}. {key}")
        lines.append(f"   Title: {summary}")
        lines.append(f"   Component: {component_names}")

        if new_owner and match_type != "none":
            if new_owner == original_owner:
                lines.append(f"   → {original_owner} (matched by {match_type}, already assigned)")
            elif match_type == "clone" and source_key:
                lines.append(f"   → {original_owner} → {new_owner} (matched by clone, source: {source_key})")
            else:
                lines.append(f"   → {original_owner} → {new_owner} (matched by {match_type})")
        else:
            lines.append(f"   → {original_owner} (no match, unchanged)")

        lines.append("")

    lines.append("=" * 60)
    if dry_run:
        lines.append(f"Summary: {will_change} will be assigned, {unchanged} unchanged")
    else:
        lines.append(f"Summary: {will_change} assigned, {unchanged} unchanged")
    lines.append("=" * 60)

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Jira FR/Defect Task Assignment - 根据标题或模块匹配自动分配 FR/Defect 任务"
    )
    parser.add_argument(
        "--owner", "-o",
        required=True,
        help="Jira 用户名（assignee）"
    )
    parser.add_argument(
        "--dry-run", "-n",
        action="store_true",
        help="预览模式，只展示分配报告，不实际执行"
    )

    args = parser.parse_args()

    # 加载分配规则
    config = load_assign_config()
    title_rules = config.get("title_rules", [])
    module_rules = config.get("module_rules", [])
    clone_source_rules = config.get("clone_source_rules", {})

    if not title_rules and not module_rules:
        print("ERROR: config.json 中未找到 title_rules 或 module_rules", file=sys.stderr)
        sys.exit(1)

    print(f"[INFO] 查询 Owner {args.owner} 名下的 FR/Defect 任务...")

    # 查询 FR 任务
    try:
        issues = search_fr_tasks(args.owner)
    except Exception as e:
        print(f"ERROR: 查询 FR 任务失败: {e}", file=sys.stderr)
        sys.exit(1)

    if not issues:
        print("[INFO] 未找到未解决的 FR/Defect 任务")
        sys.exit(0)

    print(f"[INFO] 找到 {len(issues)} 个 FR/Defect 任务")

    # 计算分配结果
    assignments = []
    for issue in issues:
        new_owner, match_type, original_owner, source_key = determine_assignment(
            issue, title_rules, module_rules, clone_source_rules
        )
        assignments.append({
            "new_owner": new_owner,
            "match_type": match_type,
            "original_owner": original_owner,
            "source_key": source_key
        })

    # 输出报告
    report = format_report(issues, assignments, args.owner, dry_run=True)
    print()
    print(report)

    # 如果是 dry-run 模式，直接退出
    if args.dry_run:
        print()
        print("[INFO] Dry-run 模式已结束")
        sys.exit(0)

    # 询问确认
    print()
    confirm = input("Continue with assignment? [y/N]: ")
    if confirm.lower() != "y":
        print("Cancelled.")
        sys.exit(0)

    # 执行分配
    print()
    print("Executing assignment...")

    changed_count = 0
    unchanged_count = 0
    changed_list = []
    failed_list = []

    for issue, assignment in zip(issues, assignments):
        key = issue.get("key")
        summary = issue.get("fields", {}).get("summary", "")
        new_owner = assignment["new_owner"]
        match_type = assignment["match_type"]
        original_owner = assignment["original_owner"]
        source_key = assignment.get("source_key")

        if new_owner and match_type != "none" and new_owner != original_owner:
            success = update_assignee(key, new_owner)
            if success:
                print(f"[OK] {key} assigned to {new_owner}")
                changed_count += 1
                changed_list.append((key, summary, original_owner, new_owner, match_type, source_key))
            else:
                print(f"[FAIL] {key} assignment failed")
                unchanged_count += 1
                failed_list.append((key, summary, new_owner))
        else:
            print(f"[SKIP] {key} unchanged")
            unchanged_count += 1

    # 输出最终分配报告
    print()
    print("=" * 60)
    print("Assignment Complete Report")
    print("=" * 60)

    if changed_list:
        print()
        print("Successfully Assigned:")
        for i, (key, summary, original, new_owner, match_type, source_key) in enumerate(changed_list, 1):
            print(f"  {i}. {key}")
            print(f"     Title: {summary}")
            if match_type == "clone" and source_key:
                print(f"     {original} → {new_owner} (matched by clone, source: {source_key})")
            else:
                print(f"     {original} → {new_owner} (matched by {match_type})")

    if failed_list:
        print()
        print("Failed Assignments:")
        for i, (key, summary, new_owner) in enumerate(failed_list, 1):
            print(f"  {i}. {key}")
            print(f"     Title: {summary}")
            print(f"     → {new_owner} (failed)")

    print()
    print(f"Total: {changed_count} assigned, {unchanged_count} unchanged, {len(failed_list)} failed")
    print("=" * 60)


if __name__ == "__main__":
    main()
