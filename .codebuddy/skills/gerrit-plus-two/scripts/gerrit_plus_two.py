#!/usr/bin/env python3
"""
Gerrit Code-Review +2（领域SE专用）一站式脚本。

工作流程：
1. 查询指定 Change 的详细信息，获取 Verified 标签状态和 commit 信息
2. 对该 Change 执行 Code-Review +2，同时添加格式化的SE评审comment
3. 若 commit 信息中包含 codereview_Report，自动完成 Confluence checklist 确认
4. 如果 Verified 已是 +1，自动调用 Submit API 合入代码
5. 输出本次 code review 的整体 review merge 状态

环境变量 / config.json（优先级: 环境变量 > config.json > 默认值）：
  GERRIT_URL       - Gerrit 服务器基础 URL（默认：http://sz.gerrit.tclcom.com:8080）
  GERRIT_USER      - Gerrit 用户名（仅环境变量，敏感信息不放入 config.json）
  GERRIT_PASS      - Gerrit HTTP 密码（仅环境变量，敏感信息不放入 config.json）
  GERRIT_SE_NAME   - 领域SE姓名（用于生成评审comment）
  GERRIT_SE_MODULE - 所属领域/模块（用于生成评审comment）
  CONFLUENCE_KEY   - Confluence API Token（用于checklist确认）

config.json 与脚本同目录，示例内容：
  {
    "GERRIT_URL": "http://sz.gerrit.tclcom.com:8080",
    "GERRIT_SE_NAME": "ZHANFENGPENG",
    "GERRIT_SE_MODULE": "FRAMEWORK",
    "CONFLUENCE_KEY": "your_token"
  }

用法：
  python3 gerrit_plus_two.py --change 879887
  python3 gerrit_plus_two.py --change Iabc123 --revision 3
  python3 gerrit_plus_two.py --change 879887 --se-name ZHANFENGPENG --module FRAMEWORK
  python3 gerrit_plus_two.py --change 879887 --skip-checklist
  python3 gerrit_plus_two.py --change 879887 --dry-run
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

DEFAULT_GERRIT_URL = "http://sz.gerrit.tclcom.com:8080"
CONFLUENCE_BASE = "https://confluence.tclking.com/rest/api"

# 多站点配置
DEFAULT_GERRIT_SITES = [
    "http://sz.gerrit.tclcom.com:8080",
    "http://hz.gerrit.tclcom.com:8081",
]

# config.json 路径（与 SKILL.md 同目录，即 scripts/ 的上级目录）
_CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json")


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


def load_config():
    """加载 config.json，返回配置字典。不存在则返回空字典。"""
    _gerrit_env_loaded()
    if os.path.isfile(_CONFIG_PATH):
        try:
            with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            print(f"WARN: config.json 解析失败 ({e})，使用默认值。", file=sys.stderr)
    return {}


def get_config(key, default=None):
    """获取配置值。优先级: 环境变量 > ~/.gerrit_env > config.json > 默认值。
    空字符串等同于未设置。"""
    _gerrit_env_loaded()
    env_val = os.environ.get(key)
    if env_val is not None and env_val != "":
        return env_val
    cfg_val = load_config().get(key)
    if cfg_val is not None and cfg_val != "":
        return cfg_val
    return default


def get_gerrit_sites():
    """获取所有配置的 Gerrit 站点列表。
    
    优先级: GERRIT_SITE 环境变量（单站点，优先） > GERRIT_SITES 环境变量 > config.json > DEFAULT_GERRIT_SITES
    """
    # GERRIT_SITE: 强制使用单一指定站点（不进行站点检测）
    env_site = os.environ.get("GERRIT_SITE")
    if env_site:
        return [env_site.rstrip("/")]
    
    # 环境变量: 逗号分隔的 URL 列表
    env_sites = os.environ.get("GERRIT_SITES")
    if env_sites:
        return [s.strip() for s in env_sites.split(",") if s.strip()]
    
    # config.json
    cfg_sites = load_config().get("GERRIT_SITES")
    if cfg_sites:
        return cfg_sites
    
    return DEFAULT_GERRIT_SITES


def find_change_site(change_id):
    """遍历所有 Gerrit 站点，查找指定 Change 所在的站点。
    
    返回 (base_url, change_info, current_revision) 元组。
    如果找到，返回站点 URL 和 Change 信息。
    如果所有站点都找不到，返回 (None, None, None)。
    """
    sites = get_gerrit_sites()
    for site in sites:
        base_url = site.rstrip("/")
        try:
            change_info, current_rev = get_change_detail(base_url, change_id)
            # 通过 status 是否为 "UNKNOWN" 或检查 project 是否存在来判断是否真的找到了
            if change_info.get("project"):
                return base_url, change_info, current_rev
        except Exception:
            continue
    return None, None, None

# ---------------------------------------------------------------------------
# 认证 & HTTP 请求
# ---------------------------------------------------------------------------

def _get_auth_header(base_url=None):
    """从环境变量或 config.json 构建 Basic Auth 头。
    
    如果指定了 base_url 且 GERRIT_SITES_CREDENTIALS 中有对应凭证，优先使用。
    """
    # 优先使用 per-site 凭证
    if base_url:
        site_creds = load_config().get("GERRIT_SITES_CREDENTIALS", {}).get(base_url)
        if site_creds:
            user = site_creds.get("user")
            password = site_creds.get("pass")
            if user and password:
                auth = f"{user}:{password}"
                encoded = base64.b64encode(auth.encode("utf-8")).decode("ascii")
                return f"Basic {encoded}"
    
    # 回退到全局凭证
    user = get_config("GERRIT_USER")
    password = get_config("GERRIT_PASS")
    if not user or not password:
        print("ERROR: GERRIT_USER 和 / 或 GERRIT_PASS 未设置（环境变量或 config.json）。", file=sys.stderr)
        print("  请设置：", file=sys.stderr)
        print("    export GERRIT_USER='your_username'", file=sys.stderr)
        print("    export GERRIT_PASS='your_http_password'", file=sys.stderr)
        print("  或在 config.json 的 GERRIT_SITES_CREDENTIALS 中配置各站点凭证", file=sys.stderr)
        sys.exit(1)

    auth = f"{user}:{password}"
    encoded = base64.b64encode(auth.encode("utf-8")).decode("ascii")
    return f"Basic {encoded}"


def gerrit_request(url, method="GET", data=None, base_url=None):
    """发送 HTTP 请求到 Gerrit REST API，返回解析后的 JSON。

    Gerrit 返回的 JSON 以 )]}' 开头（XSSI 防护），需要先去掉此前缀。
    """
    headers = {
        "Authorization": _get_auth_header(base_url),
        "Content-Type": "application/json; charset=UTF-8",
    }

    body = None
    if data is not None:
        body = json.dumps(data).encode("utf-8")

    req = urllib.request.Request(url, data=body, headers=headers, method=method)

    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read().decode("utf-8")
            # Gerrit 将 )]}' 前缀放在 JSON 开头防 XSSI
            if raw.startswith(")]}'"):
                raw = raw[4:]
            return json.loads(raw)
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
# Gerrit API 操作
# ---------------------------------------------------------------------------

def get_change_detail(base_url, change_id):
    """获取 Change 详细信息（包含标签状态）。

    返回 (change_info, current_revision) 元组。
    """
    encoded_id = urllib.parse.quote(change_id, safe="")
    url = f"{base_url}/a/changes/{encoded_id}?o=DETAILED_LABELS&o=CURRENT_REVISION"

    data = gerrit_request(url, base_url=base_url)
    current_rev = data.get("current_revision", "")
    return data, current_rev


def get_labels(change_info):
    """从 Change 详情中提取所有标签及其投票值。

    返回字典: { "Code-Review": {"value": 2, "approved": {...}}, "Verified": {"value": 1}, ... }
    """
    labels = change_info.get("labels", {})
    result = {}
    for label_name, label_info in labels.items():
        votes = {}
        all_votes = label_info.get("all", [])
        for vote in all_votes:
            username = vote.get("username", vote.get("name", "unknown"))
            votes[username] = vote.get("value", 0)

        result[label_name] = {
            "all_votes": votes,
            "recommended": label_info.get("recommended", {}),
            "disliked": label_info.get("disliked", {}),
            "approved": label_info.get("approved", {}),
            "rejected": label_info.get("rejected", {}),
        }
    return result


def check_verified_plus_one(labels):
    """检查 Verified 标签是否存在 +1 投票。

    返回 True 表示 Verified +1 存在。
    """
    verified = labels.get("Verified", {})
    all_votes = verified.get("all_votes", {})
    for _user, value in all_votes.items():
        if value >= 1:
            return True
    return False


# ---------------------------------------------------------------------------
# SSL context（Confluence 兼容旧版 TLS 重协商）
# ---------------------------------------------------------------------------

def _ssl_context():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    ctx.options |= 0x4  # OP_LEGACY_SERVER_CONNECT
    return ctx


# ---------------------------------------------------------------------------
# Commit message & codereview_Report 解析
# ---------------------------------------------------------------------------

def get_commit_message(base_url, change_id):
    """从 Gerrit 获取指定 Change 的 commit message。"""
    encoded_id = urllib.parse.quote(change_id, safe="")
    url = f"{base_url}/a/changes/{encoded_id}/revisions/current/commit"
    data = gerrit_request(url, base_url=base_url)
    return data.get("message", "")


def has_codereview_report(commit_msg):
    """检查 commit message 中是否包含 codereview_Report。"""
    for line in commit_msg.split("\n"):
        if "codereview_report" in line.lower():
            return True
    return False


def extract_codereview_page_id(commit_msg):
    """从 commit message 的 codereview_Report 行中解析 Confluence pageId。"""
    for line in commit_msg.split("\n"):
        if "codereview_report" in line.lower():
            m = re.search(r"pageId=(\d+)", line)
            if m:
                return m.group(1)
    return None


# ---------------------------------------------------------------------------
# Confluence API 操作
# ---------------------------------------------------------------------------

def _cf_headers():
    """构建 Confluence Bearer Token 认证头。"""
    token = get_config("CONFLUENCE_KEY")
    if not token:
        print("WARN: CONFLUENCE_KEY 未设置（环境变量或 config.json），将跳过 Confluence checklist 确认。", file=sys.stderr)
        return None
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def cf_get(path):
    """GET 请求 Confluence REST API。"""
    headers = _cf_headers()
    if headers is None:
        return None
    url = f"{CONFLUENCE_BASE}{path}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, context=_ssl_context()) as r:
        return json.loads(r.read())


def cf_put(path, payload):
    """PUT 请求 Confluence REST API。"""
    headers = _cf_headers()
    if headers is None:
        return None
    url = f"{CONFLUENCE_BASE}{path}"
    body = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=body, headers=headers, method="PUT")
    with urllib.request.urlopen(req, context=_ssl_context()) as r:
        return json.loads(r.read())


def get_confluence_page(page_id):
    """获取 Confluence 页面详情（含 body.storage 和 version）。"""
    return cf_get(f"/content/{page_id}?expand=body.storage,version")


def update_confluence_page(page_id, new_body, version, title):
    """更新 Confluence 页面内容，返回新版本号。"""
    payload = {
        "id": page_id,
        "type": "page",
        "title": title,
        "version": {"number": version + 1},
        "body": {
            "storage": {
                "value": new_body,
                "representation": "storage",
            }
        },
    }
    result = cf_put(f"/content/{page_id}", payload)
    return result.get("version", {}).get("number") if result else None


def mark_all_tasks_complete(body):
    """智能标记 Confluence checklist task 为完成。

    规则：
    - 单选项（只有"检查OK"）：标记为 complete
    - 双选项（"检查OK" + "不涉及"）：只标记"检查OK"为 complete，"不涉及"保持原样
    """
    def process_task_list(match):
        block = match.group(0)
        tasks = re.findall(r"<ac:task>(.*?)</ac:task>", block, re.DOTALL)
        if len(tasks) == 2:
            # 双选项：只标记"检查OK"为 complete
            def mark_ok_only(task_match):
                task_content = task_match.group(0)
                if "检查OK" in task_content:
                    task_content = task_content.replace(
                        "<ac:task-status>incomplete</ac:task-status>",
                        "<ac:task-status>complete</ac:task-status>",
                    )
                return task_content

            new_block = re.sub(r"<ac:task>.*?</ac:task>", mark_ok_only, block, flags=re.DOTALL)
            return new_block
        else:
            # 单选项：全部标记为 complete
            return block.replace(
                "<ac:task-status>incomplete</ac:task-status>",
                "<ac:task-status>complete</ac:task-status>",
            )

    return re.sub(r"<ac:task-list>.*?</ac:task-list>", process_task_list, body, flags=re.DOTALL)


def count_tasks(body):
    """统计页面中 task 的总数、未完成数和已完成数。"""
    total = len(re.findall(r"<ac:task-status>", body))
    incomplete = len(re.findall(r"<ac:task-status>incomplete</ac:task-status>", body))
    complete = len(re.findall(r"<ac:task-status>complete</ac:task-status>", body))
    return total, incomplete, complete


def do_confluence_checklist(commit_msg, dry_run=False):
    """完成 Confluence checklist 确认：将 codereview_Report 对应页面中"检查OK" task 标记为完成。

    规则：
    - 单选项（只有"检查OK"）：标记为 complete
    - 双选项（"检查OK" + "不涉及"）：只标记"检查OK"为 complete，"不涉及"保持原样

    返回状态字符串: "completed" / "skipped" / "failed" / "already_done"
    """
    if not has_codereview_report(commit_msg):
        return "not_found"

    page_id = extract_codereview_page_id(commit_msg)
    if not page_id:
        print("  [WARN] codereview_Report 存在但未找到 pageId，跳过 checklist 确认。")
        return "failed"

    print(f"  codereview_Report pageId: {page_id}")

    if dry_run:
        print(f'  [DRY RUN] 将获取 Confluence 页面 {page_id} 并标记所有"检查OK" task 为完成。')
        return "dry_run"

    # 获取页面
    page = get_confluence_page(page_id)
    if page is None:
        print("  [WARN] 无法获取 Confluence 页面（CONFLUENCE_KEY 未设置），跳过 checklist 确认。")
        return "failed"

    title = page.get("title", "?")
    version = page.get("version", {}).get("number", 0)
    body = page.get("body", {}).get("storage", {}).get("value", "")

    total, incomplete, complete = count_tasks(body)

    # 统计双选项"不涉及"的 incomplete 数量（这些不需要标记）
    dual_not_involved_incomplete = 0
    for tl in re.findall(r"<ac:task-list>.*?</ac:task-list>", body, re.DOTALL):
        tasks = re.findall(r"<ac:task>(.*?)</ac:task>", tl, re.DOTALL)
        if len(tasks) == 2:
            for task in tasks:
                if "不涉及" in task and "incomplete" in task:
                    dual_not_involved_incomplete += 1

    actionable_incomplete = incomplete - dual_not_involved_incomplete

    print(f"  Confluence 页面: {title} (v{version})")
    print(f"  Tasks: {total} 总计, {incomplete} 未完成, {complete} 已完成")
    if dual_not_involved_incomplete > 0:
        print(f'  其中 {dual_not_involved_incomplete} 个为双选项"不涉及"（无需标记）')

    if actionable_incomplete == 0:
        print('  所有"检查OK" task 已完成，无需更新。')
        return "already_done"

    # 标记"检查OK"为完成（双选项"不涉及"保持原样）
    new_body = mark_all_tasks_complete(body)
    new_version = update_confluence_page(page_id, new_body, version, title)
    if new_version:
        print(f"  [OK] Confluence checklist 已确认！页面更新至 v{new_version}。")
        print(f'  已标记 {actionable_incomplete} 个"检查OK" task 为完成。')
        return "completed"
    else:
        print("  [FAIL] Confluence 页面更新失败。")
        return "failed"


def post_review(base_url, change_id, revision, labels=None, message=None):
    """对指定 Change/Revision 发布评审（Code-Review 打分 + comment）。

    返回 Gerrit API 的响应。
    """
    encoded_id = urllib.parse.quote(change_id, safe="")
    encoded_rev = urllib.parse.quote(revision, safe="")
    url = f"{base_url}/a/changes/{encoded_id}/revisions/{encoded_rev}/review"

    payload = {}
    if labels:
        payload["labels"] = labels
    if message:
        payload["message"] = message

    return gerrit_request(url, method="POST", data=payload, base_url=base_url)


def submit_change(base_url, change_id):
    """提交（Submit）指定的 Change，将其合入代码库。

    返回 Gerrit API 的响应。
    """
    encoded_id = urllib.parse.quote(change_id, safe="")
    url = f"{base_url}/a/changes/{encoded_id}/submit"

    payload = {}  # 无需参数
    return gerrit_request(url, method="POST", data=payload, base_url=base_url)


def build_se_comment(se_name, module_name):
    """构建领域SE评审comment。

    格式：领域SE_{MODULE}_代码review_OK_{SE_NAME}
    """
    se = se_name or "SE"
    mod = module_name or "MODULE"
    return f"领域SE_{mod}_代码review_OK_{se}"


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="领域SE对指定 Gerrit Change 进行 Code-Review +2（含SE评审Comment、Confluence Checklist确认、自动Submit）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 基本用法（使用环境变量中的SE信息）
  python3 gerrit_plus_two.py --change 879887

  # 指定SE姓名和模块
  python3 gerrit_plus_two.py --change 879887 --se-name ZHANFENGPENG --module FRAMEWORK

  # 指定 revision
  python3 gerrit_plus_two.py --change Iabc123def456 --revision 3

  # 跳过 Confluence checklist 确认
  python3 gerrit_plus_two.py --change 879887 --skip-checklist

  # Preview 模式
  python3 gerrit_plus_two.py --change 879887 --dry-run
        """,
    )

    parser.add_argument(
        "--change", "-c",
        required=True,
        help="Gerrit Change ID（数字编号或 I 开头的完整 ID）",
    )
    parser.add_argument(
        "--revision", "-r",
        default="current",
        help="Patch Set 编号（默认：current）",
    )
    parser.add_argument(
        "--se-name",
        default=get_config("GERRIT_SE_NAME", ""),
        help="领域SE姓名（优先级: $GERRIT_SE_NAME > config.json）",
    )
    parser.add_argument(
        "--module",
        default=get_config("GERRIT_SE_MODULE", ""),
        help="所属领域/模块（优先级: $GERRIT_SE_MODULE > config.json）",
    )
    parser.add_argument(
        "--url", "-u",
        default=get_config("GERRIT_URL", DEFAULT_GERRIT_URL),
        help=f"Gerrit 基础 URL（优先级: $GERRIT_URL > config.json > {DEFAULT_GERRIT_URL}）",
    )
    parser.add_argument(
        "--skip-checklist",
        action="store_true",
        help="跳过 Confluence checklist 确认步骤",
    )
    parser.add_argument(
        "--dry-run", "-n",
        action="store_true",
        help="预览模式，仅查看状态不执行任何修改操作",
    )

    args = parser.parse_args()
    
    # 确定要使用的 Gerrit 站点
    # GERRIT_SITE: 单站点强制指定（不进行站点检测）
    # --url: 手动指定站点
    # 否则: 自动遍历站点检测
    gerrit_site_env = os.environ.get("GERRIT_SITE")
    
    if gerrit_site_env:
        base_url = gerrit_site_env.rstrip("/")
        explicit_url = True
        site_mode = f"单站点 (GERRIT_SITE)"
    elif args.url and args.url != get_config("GERRIT_URL", DEFAULT_GERRIT_URL):
        base_url = args.url.rstrip("/")
        explicit_url = True
        site_mode = "手动指定 (--url)"
    else:
        explicit_url = False
        site_mode = None

    # 构建SE评审comment
    se_comment = build_se_comment(args.se_name, args.module)

    print("=" * 60)
    if args.dry_run:
        print("  [预览] 预览模式 — 仅查看状态，不执行任何修改")
    else:
        print("  [执行] 领域SE Gerrit Code-Review +2")
    print("=" * 60)
    if site_mode:
        print(f"  站点模式:    {site_mode}")
    print(f"  Change ID:    {args.change}")
    print(f"  Revision:     {args.revision}")
    print(f"  SE Comment:   {se_comment}")
    print(f"  Checklist:    {'跳过' if args.skip_checklist else '自动确认'}")

    # -----------------------------------------------------------------------
    # 步骤 1: 获取 Change 详细信息 & 标签状态 & commit 信息
    # -----------------------------------------------------------------------
    print(f"\n{'─' * 50}")
    print("  步骤 1: 查询 Change 信息 & 标签状态")
    print(f"{'─' * 50}")

    if gerrit_site_env:
        # 使用 GERRIT_SITE 指定的单站点，不进行检测
        print(f"  Gerrit URL:   {base_url} ({site_mode})")
        change_info, current_revision = get_change_detail(base_url, args.change)
    elif explicit_url:
        # 使用用户指定的 URL
        print(f"  Gerrit URL:   {base_url} ({site_mode})")
        change_info, current_revision = get_change_detail(base_url, args.change)
    else:
        # 自动检测 Change 所在的 Gerrit 站点
        print(f"  正在自动检测 Change 所在的 Gerrit 站点...")
        sites = get_gerrit_sites()
        found = False
        for site in sites:
            site_url = site.rstrip("/")
            print(f"    尝试: {site_url}")
            try:
                change_info, current_revision = get_change_detail(site_url, args.change)
                if change_info.get("project"):
                    base_url = site_url
                    found = True
                    print(f"    [OK] 找到 Change!")
                    break
            except Exception as e:
                print(f"    [X] {site_url} 未找到或无法访问")
                continue
        
        if not found:
            print(f"  [ERROR] 在所有站点都未找到 Change {args.change}")
            sys.exit(1)
    
    # 后续使用 base_url 进行所有操作
    print(f"  使用 Gerrit:  {base_url}")
    subject = change_info.get("subject", "(无标题)")
    project = change_info.get("project", "(未知)")
    status = change_info.get("status", "(未知)")
    labels = get_labels(change_info)

    print(f"  项目:     {project}")
    print(f"  标题:     {subject}")
    print(f"  状态:     {status}")
    print(f"  当前 Rev: {current_revision}")

    # 显示当前标签状态
    print(f"\n  当前标签状态：")
    for label_name, label_info in sorted(labels.items()):
        all_votes = label_info.get("all_votes", {})
        if all_votes:
            vote_strs = [f"{user}: {val:+d}" for user, val in all_votes.items()]
            print(f"    {label_name}: {', '.join(vote_strs)}")
        else:
            print(f"    {label_name}: (无投票)")

    # 检查 Verified +1
    verified_ok = check_verified_plus_one(labels)

    # 获取 commit message（用于后续 checklist 确认）
    print(f"\n  获取 commit message ...")
    commit_msg = get_commit_message(base_url, args.change)
    has_report = has_codereview_report(commit_msg)
    print(f"  codereview_Report: {'存在' if has_report else '无'}")

    if verified_ok:
        print(f"\n  [OK] Verified 已 +1，满足提交条件")
    else:
        print(f"\n  [等待] Verified 未 +1，本次仅做 Code-Review +2")

    # -----------------------------------------------------------------------
    # 步骤 2: Code-Review +2 和 SE评审Comment（分两次独立请求）
    # -----------------------------------------------------------------------
    print(f"\n{'─' * 50}")
    print("  步骤 2: 执行 Code-Review +2 并添加 SE 评审 Comment")
    print(f"{'─' * 50}")

    review_ok = False
    comment_ok = False
    if args.dry_run:
        print(f"  [DRY RUN] 将对 Change {args.change} (rev {args.revision}) 给予 Code-Review +2")
        print(f"  [DRY RUN] 标签: {{\"Code-Review\": \"+2\"}}")
        print(f"  [DRY RUN] 将添加独立 Comment: \"{se_comment}\"")
        review_ok = True
        comment_ok = True
    else:
        # ---- Step 2a: Code-Review +2（仅投票，不带comment） ----
        print(f"  2a. 正在对 Change {args.change} (rev {args.revision}) 给予 Code-Review +2 ...")
        review_result = post_review(
            base_url,
            args.change,
            args.revision,
            labels={"Code-Review": "+2"},
        )
        if "labels" in review_result or "change" in str(review_result).lower():
            print(f"      [OK] Code-Review +2 成功！")
            review_ok = True
        else:
            print(f"      [WARN] Code-Review +2 响应异常，请检查。")
            print(f"      响应: {json.dumps(review_result, indent=2, ensure_ascii=False)}")
            review_ok = False

        # ---- Step 2b: SE评审Comment（独立的review comment） ----
        print(f"  2b. 正在添加 SE 评审 Comment: \"{se_comment}\" ...")
        comment_result = post_review(
            base_url,
            args.change,
            args.revision,
            message=se_comment,
        )
        # 纯评论请求 Gerrit 可能返回 {}，HTTP 200 即视为成功
        if isinstance(comment_result, dict):
            print(f"      [OK] SE评审Comment 已独立添加。")
            comment_ok = True
        else:
            print(f"      [WARN] SE评审Comment 添加异常，请检查。")
            print(f"      响应: {json.dumps(comment_result, indent=2, ensure_ascii=False)}")
            comment_ok = False

    # -----------------------------------------------------------------------
    # 步骤 3: Confluence Checklist 确认
    # -----------------------------------------------------------------------
    checklist_status = "skipped"
    print(f"\n{'─' * 50}")
    print("  步骤 3: Confluence Checklist 确认")
    print(f"{'─' * 50}")

    if args.skip_checklist:
        print("  已跳过 Confluence checklist 确认（--skip-checklist）。")
        checklist_status = "skipped"
    elif not has_report:
        print("  commit 信息中无 codereview_Report，跳过 checklist 确认。")
        checklist_status = "not_found"
    else:
        print("  检测到 codereview_Report，开始确认 Confluence checklist ...")
        checklist_status = do_confluence_checklist(commit_msg, dry_run=args.dry_run)
        if checklist_status == "dry_run":
            checklist_status = "skipped"  # dry-run 不实际修改

    # -----------------------------------------------------------------------
    # 步骤 4: 如果需要，执行 Submit
    # -----------------------------------------------------------------------
    submit_success = False
    # 先检查 Change 是否已合并
    already_merged = (status == "MERGED")

    if already_merged:
        print(f"\n{'─' * 50}")
        print("  步骤 4: 自动 Submit（合并入代码库）")
        print(f"{'─' * 50}")
        print(f"  [OK] Change 已是 MERGED 状态，无需重复 Submit。")
        submit_success = True
    elif verified_ok:
        print(f"\n{'─' * 50}")
        print("  步骤 4: 自动 Submit（合并入代码库）")
        print(f"{'─' * 50}")

        if args.dry_run:
            print(f"  [DRY RUN] 将 Submit Change {args.change}")
            submit_success = "dry_run"
        else:
            print(f"  正在 Submit Change {args.change} ...")
            try:
                submit_result = submit_change(base_url, args.change)
                if isinstance(submit_result, dict):
                    new_status = submit_result.get("status", "")
                    if new_status == "MERGED":
                        print(f"  [OK] Submit 成功！Change 已合并。")
                        submit_success = True
                    else:
                        print(f"  [WARN] Submit 返回状态: {new_status}")
                        print(f"  响应: {json.dumps(submit_result, indent=2, ensure_ascii=False)}")
                        submit_success = False
                else:
                    print(f"  [WARN] Submit 响应异常。")
                    submit_success = False
            except SystemExit:
                print(f"  [FAIL] Submit 失败，请检查 Gerrit 日志。")
                submit_success = False
    else:
        print(f"\n{'─' * 50}")
        print("  步骤 4: 跳过 Submit（Verified 未 +1）")
        print(f"{'─' * 50}")
        print(f"  等待 Verified +1 后再合并。")

    # -----------------------------------------------------------------------
    # 步骤 5: 最终状态汇总（整体 Review Merge 状态反馈）
    # -----------------------------------------------------------------------
    print(f"\n{'=' * 60}")
    print("  [汇总] 最终 Review Merge 状态")
    print(f"{'=' * 60}")
    print(f"  Change ID:       {args.change}")
    print(f"  项目/标题:       {project} / {subject}")

    # Code-Review +2 状态
    if review_ok:
        print(f"  Code-Review +2:  [OK] 已给予（独立投票）")
    else:
        print(f"  Code-Review +2:  [FAIL] 失败")

    # SE Comment 状态
    if comment_ok:
        print(f"  SE Comment:      [OK] {se_comment}（独立comment）")
    else:
        print(f"  SE Comment:      [FAIL] 添加失败")

    # Confluence Checklist 状态
    checklist_display = {
        "completed":    "[OK] 已全部确认",
        "already_done": "[OK] 已全部完成（无需更新）",
        "not_found":    "无 codereview_Report",
        "skipped":      "已跳过",
        "failed":       "[WARN] 确认失败",
        "dry_run":      "[DRY RUN]",
    }
    print(f"  Checklist:       {checklist_display.get(checklist_status, '未知')}")

    # Verified 状态
    if verified_ok:
        print(f"  Verified:        +1 [OK]")
    else:
        print(f"  Verified:        未 +1 [等待]")

    # Submit 状态 & 整体结论
    print(f"\n  {'─' * 40}")
    if submit_success is True:
        if already_merged:
            print(f"  Submit:          已是 MERGED [OK]")
            print(f"  整体状态:        [MERGED] 已合并 -- Code-Review +2 已完成，Checklist 已确认，Change 此前已合并")
        else:
            print(f"  Submit:          成功 [OK]")
            print(f"  整体状态:        [MERGED] 已合并 -- Code-Review +2 已完成，Checklist 已确认，Submit 成功")
    elif submit_success == "dry_run":
        print(f"  Submit:          [DRY RUN 跳过]")
        print(f"  整体状态:        [预览] 预览模式")
    else:
        if verified_ok:
            print(f"  Submit:          失败 [FAIL]")
            print(f"  整体状态:        [ERROR] Code-Review +2 已完成，Checklist 已处理，但 Submit 失败")
        else:
            print(f"  Submit:          跳过（等待 Verified +1）")
            print(f"  整体状态:        [PENDING] Code-Review +2 已完成，Checklist 已处理，等待 Verified +1 后合并")

    print(f"{'=' * 60}")

    # 返回码：Submit 失败时返回非零
    if submit_success is False and verified_ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
