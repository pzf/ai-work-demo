#!/usr/bin/env python3
"""
Jira Fast Track - 快速流转无代码关联的 Jira 单

工作流程：
1. 检测 Jira Description 中是否包含 Gerrit 链接
2. 根据当前状态确定需要的 transition 步骤
3. 自动填充各 transition 所需的固定字段值
4. 执行状态流转并添加 Comment

环境变量 / config.json（优先级: 环境变量 > config.json > 默认值）：
  JIRA_URL       - Jira 服务器基础 URL
  JIRA_USER      - Jira 用户名
  JIRA_PASS      - Jira 密码或 API Token

用法：
  python3 jira_fast_track.py --issue ANDROID-12345
  python3 jira_fast_track.py --jql "project=ANDROID AND status in (Assigned,Accept,Develop)"
  python3 jira_fast_track.py --issue ANDROID-12345 --dry-run
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

# Gerrit 链接检测正则
GERRIT_PATTERN = re.compile(r'https?://sz\.gerrit\.tclcom\.com')


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

# 字段填充值定义
# - option 类型字段: {"id": "xxx"}
# - array 类型字段: [{"id": "xxx"}]
# - number 类型字段: 直接数值（不是字符串）
FIELDS_ASSIGNED_TO_ACCEPT = {
    "customfield_16000": {"id": "23807"},       # Ergo Related: NO (id=23807)
    "customfield_11584": [{"id": "12579"}],      # Solution Type: TCT ROM Support (id=12579)
    "customfield_11594": {"id": "12428"},        # R&D Confirm: Yes (id=12428)
    "customfield_11574": "default support",       # R&D Comments: 字符串
    "customfield_14225": 0,                      # SW workload(MD): 数值
    "customfield_11592": {"id": "12425"},        # TCT Customized: NO (id=12425)
}

FIELDS_DEVELOP_TO_VERIFIED_SW = {
    "customfield_12310": [{"id": "14258"}],       # Team For Checking: TCT ROM (id=14258)
    "customfield_16000": {"id": "23807"},         # Ergo Related: NO (id=23807)
    "customfield_12697": "Google XTS cover",      # Additional DEV Comment: 字符串
    "customfield_24401": {"id": "53302"},         # R&D自测结果: 无自测条件 (id=53302)
    "customfield_25911": {"id": "55333"},         # COTA Confirm: NO (id=55333)
}

# 字段 ID 到可读名称的映射
FIELD_DISPLAY_NAMES = {
    "customfield_16000": "Ergo Related",
    "customfield_11584": "Solution Type",
    "customfield_11594": "R&D Confirm",
    "customfield_11574": "R&D Comments",
    "customfield_14225": "SW workload(MD)",
    "customfield_11592": "TCT Customized",
    "customfield_12310": "Team For Checking",
    "customfield_12697": "Additional DEV Comment",
    "customfield_24401": "R&D自测结果",
    "customfield_25911": "COTA Confirm",
}

# config.json 路径
_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")


def load_config():
    """加载 config.json，返回配置字典。"""
    if os.path.isfile(_CONFIG_PATH):
        try:
            with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            print(f"WARN: config.json 解析失败 ({e})，使用默认值。", file=sys.stderr)
    return {}


def get_config(key, default=None):
    """获取配置值。优先级: ~/.gerrit_env > config.json > 默认值。"""
    # Always read from ~/.gerrit_env first, ignore environment variables
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
    cfg_val = load_config().get(key)
    if cfg_val is not None and cfg_val != "":
        return cfg_val
    return default


# ---------------------------------------------------------------------------
# 认证 & HTTP 请求
# ---------------------------------------------------------------------------

def _get_auth_header():
    """构建认证头。优先使用 Bearer Token，其次使用 Basic Auth。"""
    user = get_config("JIRA_USER")
    password = get_config("JIRA_PASS")
    
    # 如果配置了 Bearer Token（以 AT 开头或其他情况），使用 Bearer Auth
    if password:
        # 尝试验证是否是有效的 base64 token（Atlassian API token 格式）
        try:
            decoded = base64.b64decode(password).decode('utf-8', errors='ignore')
            # 如果解码后包含冒号，可能是 username:token 格式，使用 Basic Auth
            if ':' not in decoded:
                # 尝试作为 Bearer Token
                return f"Bearer {password}"
        except:
            pass
        
        # 如果密码看起来像 API token（非纯数字密码），尝试 Bearer
        if not password.isdigit():
            return f"Bearer {password}"
    
    # 回退到 Basic Auth
    if not user or not password:
        print("ERROR: JIRA_USER 和/或 JIRA_PASS 未设置（环境变量或 config.json）。", file=sys.stderr)
        print("  请设置：", file=sys.stderr)
        print("    export JIRA_USER='your_username'", file=sys.stderr)
        print("    export JIRA_PASS='your_password_or_api_token'", file=sys.stderr)
        sys.exit(1)

    auth = f"{user}:{password}"
    encoded = base64.b64encode(auth.encode("utf-8")).decode("ascii")
    return f"Basic {encoded}"


def jira_request(endpoint, method="GET", data=None):
    """发送 HTTP 请求到 Jira REST API，返回解析后的 JSON。"""
    base_url = get_config("JIRA_URL", DEFAULT_JIRA_URL)
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


def jira_request_post(endpoint, data=None):
    """发送 POST 请求到 Jira REST API，Jira 的 POST transition 返回空响应。"""
    base_url = get_config("JIRA_URL", DEFAULT_JIRA_URL)
    url = f"{base_url}/rest/api/2/{endpoint}"

    headers = {
        "Authorization": _get_auth_header(),
        "Content-Type": "application/json; charset=UTF-8",
    }

    body = None
    if data is not None:
        body = json.dumps(data).encode("utf-8")

    req = urllib.request.Request(url, data=body, headers=headers, method="POST")

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

def get_issue(issue_key):
    """获取 Jira Issue 详情。"""
    return jira_request(f"issue/{issue_key}")


def get_issue_transitions(issue_key):
    """获取 Issue 可用的 transitions。"""
    return jira_request(f"issue/{issue_key}/transitions")


def do_transition(issue_key, transition_id, fields=None, comment=None):
    """执行 transition。"""
    payload = {"transition": {"id": str(transition_id)}}

    if fields:
        payload["fields"] = fields

    if comment:
        payload["update"] = {"comment": [{"add": {"body": comment}}]}

    return jira_request_post(f"issue/{issue_key}/transitions", data=payload)


def search_issues(jql, max_results=100):
    """使用 JQL 搜索 issues。"""
    params = urllib.parse.urlencode({
        "jql": jql,
        "maxResults": max_results,
        "fields": "key,status,description"
    })
    return jira_request(f"search?{params}")


def add_comment(issue_key, comment_body):
    """添加 Comment。"""
    payload = {"body": comment_body}
    return jira_request(f"issue/{issue_key}/comment", method="POST", data=payload)


# ---------------------------------------------------------------------------
# 业务逻辑
# ---------------------------------------------------------------------------

def has_gerrit_link(description):
    """检查 Description 中是否包含 Gerrit 链接。"""
    if not description:
        return False
    return bool(GERRIT_PATTERN.search(description))


def get_status_name(status_info):
    """从 status info 中提取状态名称。"""
    return status_info.get("name", "") if status_info else ""


def find_transition_by_name(transitions, name):
    """在 transitions 列表中查找指定名称的 transition。"""
    for t in transitions.get("transitions", []):
        if t.get("name", "").lower() == name.lower():
            return t.get("id")
    return None


def determine_transition_steps(current_status):
    """根据当前状态确定需要执行的 transition 步骤。"""
    status = current_status.lower()

    steps = []

    if status in ("assigned", "to do"):
        steps = [
            ("accept", "Assigned → Accept", FIELDS_ASSIGNED_TO_ACCEPT),
            ("develop", "Accept → Develop", None),
            ("in work", "Develop → In Work", None),
            ("verified_sw", "In Work → Verified_SW", FIELDS_DEVELOP_TO_VERIFIED_SW),
            ("deliver", "Verified_SW → Delivered", None),
        ]
    elif status in ("accept", "in progress"):
        steps = [
            ("develop", "Accept/Progress → Develop", None),
            ("in work", "Develop → In Work", None),
            ("verified_sw", "In Work → Verified_SW", FIELDS_DEVELOP_TO_VERIFIED_SW),
            ("deliver", "Verified_SW → Delivered", None),
        ]
    elif status == "develop":
        steps = [
            ("in work", "Develop → In Work", None),
            ("verified_sw", "In Work → Verified_SW", FIELDS_DEVELOP_TO_VERIFIED_SW),
            ("deliver", "Verified_SW → Delivered", None),
        ]
    elif status == "in work":
        steps = [
            ("verified_sw", "In Work → Verified_SW", FIELDS_DEVELOP_TO_VERIFIED_SW),
            ("deliver", "Verified_SW → Delivered", None),
        ]
    elif status == "verified_sw":
        steps = [
            ("deliver", "Verified_SW → Delivered", None),
        ]
    elif status == "delivered":
        steps = []

    return steps


def process_issue(issue_key, dry_run=False):
    """处理单个 Jira Issue。"""
    print(f"\n{'='*60}")
    print(f"Issue: {issue_key}")
    print(f"{'='*60}")

    # 获取 Issue 详情
    try:
        issue = get_issue(issue_key)
    except Exception as e:
        print(f"[ERROR] 无法获取 Issue {issue_key}: {e}")
        return False

    # 检查 Description 中是否有 Gerrit 链接
    description = issue.get("fields", {}).get("description", "")
    if has_gerrit_link(description):
        print(f"[SKIP] Issue {issue_key} 包含 Gerrit 链接，视为有代码关联")
        return False

    # 获取当前状态
    current_status = get_status_name(issue.get("fields", {}).get("status", {}))
    print(f"当前状态: {current_status}")

    # 确定需要执行的 transition 步骤
    steps = determine_transition_steps(current_status)

    if not steps:
        if current_status.lower() == "delivered":
            print(f"[SKIP] Issue {issue_key} 已处于 Delivered 状态")
        else:
            print(f"[SKIP] Issue {issue_key} 状态 '{current_status}' 无可执行的 transition")
        return False

    print(f"将执行 {len(steps)} 个 transition:")

    # 执行每个 transition（每次重新获取当前可用 transitions）
    for step_name, step_desc, fields in steps:
        print(f"\n- {step_desc}")

        if fields:
            print(f"  填充字段:")
            for field_id, field_value in fields.items():
                display_name = FIELD_DISPLAY_NAMES.get(field_id, field_id)
                print(f"    {display_name} ({field_id}) = {field_value}")

        if dry_run:
            print(f"  [Dry-run] 跳过实际执行")
            continue

        # 获取当前可用的 transitions
        try:
            transitions_data = get_issue_transitions(issue_key)
        except Exception as e:
            print(f"  [ERROR] 无法获取 transitions: {e}")
            print(f"  [INFO] 继续处理下一个 Issue")
            return False

        # 查找当前步骤需要的 transition
        transition_id = None
        for t in transitions_data.get("transitions", []):
            if t.get("name", "").lower() == step_name.lower():
                transition_id = t.get("id")
                break

        if not transition_id:
            print(f"  [WARN] 找不到 transition '{step_name}'，跳过此步骤")
            continue

        # 执行 transition
        try:
            do_transition(issue_key, transition_id, fields=fields)
            print(f"  [OK] Transition 执行成功")
        except Exception as e:
            print(f"  [ERROR] Transition 执行失败: {e}")
            print(f"  [INFO] 继续处理下一个 Issue")
            return False

    print(f"\n[OK] Issue {issue_key} 处理完成")
    return True


def main():
    parser = argparse.ArgumentParser(
        description="Jira Fast Track - 快速流转无代码关联的 Jira 单"
    )
    parser.add_argument(
        "--issue", "-i",
        help="Jira Issue 号（如 ANDROID-12345）"
    )
    parser.add_argument(
        "--jql", "-q",
        help="JQL 查询语句"
    )
    parser.add_argument(
        "--dry-run", "-n",
        action="store_true",
        help="预览模式，不实际执行任何操作"
    )

    args = parser.parse_args()

    # 验证参数
    if not args.issue and not args.jql:
        parser.error("必须指定 --issue 或 --jql")

    if args.issue and args.jql:
        parser.error("不能同时指定 --issue 和 --jql")

    # 确认非 dry-run 模式
    if not args.dry_run:
        print("=" * 60)
        print("WARNING: 即将执行实际的 Jira 状态变更！")
        print("=" * 60)
        confirm = input("确认执行? (yes/no): ")
        if confirm.lower() not in ("yes", "y"):
            print("已取消执行")
            sys.exit(0)

    if args.dry_run:
        print("[INFO] Dry-run 模式：仅预览，不实际执行")

    print()

    # 处理 Issue(s)
    if args.issue:
        issues = [args.issue]
    else:
        print(f"[INFO] 执行 JQL 查询: {args.jql}")
        try:
            result = search_issues(args.jql)
            issues = [issue["key"] for issue in result.get("issues", [])]
            print(f"[INFO] 找到 {len(issues)} 个 Issues")
        except Exception as e:
            print(f"[ERROR] JQL 查询失败: {e}")
            sys.exit(1)

    # 逐个处理
    success_count = 0
    fail_count = 0

    for issue_key in issues:
        if process_issue(issue_key, dry_run=args.dry_run):
            success_count += 1
        else:
            fail_count += 1

    # 输出摘要
    print(f"\n{'='*60}")
    print(f"处理完成:")
    print(f"  成功: {success_count}")
    print(f"  失败: {fail_count}")
    print(f"  总计: {len(issues)}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
