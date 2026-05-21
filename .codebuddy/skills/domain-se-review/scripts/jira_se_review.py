#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
domain-se-review - Independent SE review script

Workflow:
1. Parse Jira issue, extract Gerrit links from description
2. For each Gerrit Change: Code-Review +2, SE comment, Confluence checklist, Submit
3. Transition Jira issue status

Config priority: env vars > ~/.gerrit_env > config.json
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
DEFAULT_GERRIT_SITES = [
    "http://sz.gerrit.tclcom.com:8080",
    "http://hz.gerrit.tclcom.com:8081",
]
CONFLUENCE_BASE = "https://confluence.tclking.com/rest/api"
DEFAULT_JIRA_URL = "https://jira.tclking.com"

# SSL context for legacy TLS support
def _ssl_context():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    ctx.options |= 0x4  # OP_LEGACY_SERVER_CONNECT
    return ctx

GERRIT_LINK_PATTERN = re.compile(
    r'(https?://[^\s"\'<>]+/c/[^\s"\'<>]+/\+/\d+|'
    r'https?://[^\s"\'<>]+/\d+|'
    r'\b\d{5,}\b)',
    re.IGNORECASE
)


# ============================================================================
# Config Loading
# ============================================================================

def load_gerrit_env():
    """Load config from ~/.gerrit_env into environment variables if not already set."""
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


def get_config(key, default=None):
    _gerrit_env_loaded()
    env_val = os.environ.get(key)
    if env_val is not None and env_val != "":
        return env_val
    cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config.json")
    if os.path.isfile(cfg_path):
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                val = cfg.get(key)
                if val:
                    return val
        except Exception:
            pass
    return default


def get_gerrit_sites():
    env_sites = os.environ.get("GERRIT_SITES")
    if env_sites:
        return [s.strip() for s in env_sites.split(",") if s.strip()]
    return DEFAULT_GERRIT_SITES


# ============================================================================
# SSL Context
# ============================================================================

def _ssl_context():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    ctx.options |= 0x4
    return ctx


# ============================================================================
# Gerrit HTTP
# ============================================================================

def _get_auth_header(base_url=None):
    _gerrit_env_loaded()
    sites_creds_str = os.environ.get("GERRIT_SITES_CREDENTIALS", "")
    if sites_creds_str and base_url:
        try:
            sites_creds = json.loads(sites_creds_str)
            creds = sites_creds.get(base_url)
            if creds:
                user = creds.get("user") or os.environ.get("GERRIT_USER")
                password = creds.get("pass") or os.environ.get("GERRIT_PASS")
                if user and password:
                    auth = f"{user}:{password}"
                    encoded = base64.b64encode(auth.encode("utf-8")).decode("ascii")
                    return f"Basic {encoded}"
        except (json.JSONDecodeError, TypeError):
            pass

    user = os.environ.get("GERRIT_USER")
    password = os.environ.get("GERRIT_PASS")
    if not user or not password:
        print("ERROR: GERRIT_USER / GERRIT_PASS not set", file=sys.stderr)
        sys.exit(1)
    auth = f"{user}:{password}"
    encoded = base64.b64encode(auth.encode("utf-8")).decode("ascii")
    return f"Basic {encoded}"


def gerrit_request(url, method="GET", data=None, base_url=None):
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
            if raw.startswith(")]}'"):
                raw = raw[4:]
            return json.loads(raw)
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        print(f"ERROR: HTTP {e.code} - {e.reason}", file=sys.stderr)
        print(f"  URL: {url}", file=sys.stderr)
        print(f"  Response: {err_body}", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"ERROR: Connection failed - {e.reason}", file=sys.stderr)
        sys.exit(1)


def find_change_site(change_id):
    sites = get_gerrit_sites()
    for site in sites:
        base_url = site.rstrip("/")
        try:
            encoded_id = urllib.parse.quote(change_id, safe="")
            url = f"{base_url}/a/changes/{encoded_id}?o=DETAILED_LABELS&o=CURRENT_REVISION"
            data = gerrit_request(url, base_url=base_url)
            if data.get("project"):
                current_rev = data.get("current_revision", "")
                return base_url, data, current_rev
        except Exception:
            continue
    return None, None, None


def get_change_detail(base_url, change_id):
    encoded_id = urllib.parse.quote(change_id, safe="")
    url = f"{base_url}/a/changes/{encoded_id}?o=DETAILED_LABELS&o=CURRENT_REVISION"
    data = gerrit_request(url, base_url=base_url)
    return data, data.get("current_revision", "")


def get_labels(change_info):
    labels = change_info.get("labels", {})
    result = {}
    for label_name, label_info in labels.items():
        votes = {}
        for vote in label_info.get("all", []):
            username = vote.get("username", vote.get("name", "unknown"))
            votes[username] = vote.get("value", 0)
        result[label_name] = {"all_votes": votes}
    return result


def check_verified_plus_one(labels):
    verified = labels.get("Verified", {})
    for _user, value in verified.get("all_votes", {}).items():
        if value >= 1:
            return True
    return False


def get_commit_message(base_url, change_id):
    encoded_id = urllib.parse.quote(change_id, safe="")
    url = f"{base_url}/a/changes/{encoded_id}/revisions/current/commit"
    data = gerrit_request(url, base_url=base_url)
    return data.get("message", "")


def has_codereview_report(commit_msg):
    return "codereview_report" in commit_msg.lower()


def extract_codereview_page_id(commit_msg):
    for line in commit_msg.split("\n"):
        if "codereview_report" in line.lower():
            m = re.search(r"pageId=(\d+)", line)
            if m:
                return m.group(1)
    return None


def post_review(base_url, change_id, revision, labels=None, message=None):
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
    encoded_id = urllib.parse.quote(change_id, safe="")
    url = f"{base_url}/a/changes/{encoded_id}/submit"
    return gerrit_request(url, method="POST", data={}, base_url=base_url)


# ============================================================================
# Confluence API
# ============================================================================

def _cf_headers():
    token = get_config("CONFLUENCE_KEY")
    if not token:
        return None
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def cf_get(path):
    headers = _cf_headers()
    if headers is None:
        return None
    url = f"{CONFLUENCE_BASE}{path}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, context=_ssl_context()) as r:
            return json.loads(r.read())
    except Exception:
        return None


def cf_put(path, payload):
    headers = _cf_headers()
    if headers is None:
        return None
    url = f"{CONFLUENCE_BASE}{path}"
    body = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=body, headers=headers, method="PUT")
    try:
        with urllib.request.urlopen(req, context=_ssl_context()) as r:
            return json.loads(r.read())
    except Exception:
        return None


def get_confluence_page(page_id):
    return cf_get(f"/content/{page_id}?expand=body.storage,version")


def update_confluence_page(page_id, new_body, version, title):
    payload = {
        "id": page_id,
        "type": "page",
        "title": title,
        "version": {"number": version + 1},
        "body": {"storage": {"value": new_body, "representation": "storage"}},
    }
    result = cf_put(f"/content/{page_id}", payload)
    return result.get("version", {}).get("number") if result else None


def mark_all_tasks_complete(body):
    def process_task_list(match):
        block = match.group(0)
        tasks = re.findall(r"<ac:task>(.*?)</ac:task>", block, re.DOTALL)
        if len(tasks) == 2:
            def mark_ok_only(tm):
                content = tm.group(0)
                if "\u68c0\u67e5OK" in content:
                    content = content.replace(
                        "<ac:task-status>incomplete</ac:task-status>",
                        "<ac:task-status>complete</ac:task-status>",
                    )
                return content
            return re.sub(r"<ac:task>.*?</ac:task>", mark_ok_only, block, flags=re.DOTALL)
        else:
            return block.replace(
                "<ac:task-status>incomplete</ac:task-status>",
                "<ac:task-status>complete</ac:task-status>",
            )
    return re.sub(r"<ac:task-list>.*?</ac:task-list>", process_task_list, body, flags=re.DOTALL)


def count_tasks(body):
    total = len(re.findall(r"<ac:task-status>", body))
    incomplete = len(re.findall(r"<ac:task-status>incomplete</ac:task-status>", body))
    complete = len(re.findall(r"<ac:task-status>complete</ac:task-status>", body))
    return total, incomplete, complete


def do_confluence_checklist(commit_msg, dry_run=False):
    if not has_codereview_report(commit_msg):
        return "not_found"
    page_id = extract_codereview_page_id(commit_msg)
    if not page_id:
        print("  [WARN] codereview_Report found but pageId not found")
        return "failed"
    if dry_run:
        return "skipped"
    page = get_confluence_page(page_id)
    if page is None:
        print("  [WARN] Cannot fetch Confluence page (CONFLUENCE_KEY not set or invalid)")
        return "failed"
    title = page.get("title", "?")
    version = page.get("version", {}).get("number", 0)
    body = page.get("body", {}).get("storage", {}).get("value", "")
    total, incomplete, complete = count_tasks(body)
    dual_not_involved = sum(
        1 for tl in re.findall(r"<ac:task-list>.*?</ac:task-list>", body, re.DOTALL)
        for t in re.findall(r"<ac:task>(.*?)</ac:task>", tl, re.DOTALL)
        if "\u4e0d\u6d89\u53ca" in t and "incomplete" in t
    )
    actionable = incomplete - dual_not_involved
    if actionable == 0:
        return "already_done"
    new_body = mark_all_tasks_complete(body)
    new_version = update_confluence_page(page_id, new_body, version, title)
    if new_version:
        return "completed"
    return "failed"


# ============================================================================
# Jira API
# ============================================================================

def _jira_auth_header():
    user = get_config("JIRA_USER")
    password = get_config("JIRA_PASS")
    if not user or not password:
        print("ERROR: JIRA_USER / JIRA_PASS not set", file=sys.stderr)
        sys.exit(1)
    if not password.isdigit() and ":" not in password:
        return f"Bearer {password}"
    auth = f"{user}:{password}"
    encoded = base64.b64encode(auth.encode("utf-8")).decode("ascii")
    return f"Basic {encoded}"


def jira_request(endpoint, method="GET", data=None):
    base_url = get_config("JIRA_URL", DEFAULT_JIRA_URL)
    url = f"{base_url}/rest/api/2/{endpoint}"
    headers = {
        "Authorization": _jira_auth_header(),
        "Content-Type": "application/json; charset=UTF-8",
    }
    body = None
    if data is not None:
        body = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, context=_ssl_context()) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        print(f"ERROR: HTTP {e.code} - {e.reason}", file=sys.stderr)
        print(f"  Response: {err_body}", file=sys.stderr)
        sys.exit(1)


def get_issue(issue_key):
    return jira_request(f"issue/{issue_key}")


def get_issue_transitions(issue_key):
    return jira_request(f"issue/{issue_key}/transitions")


def do_transition(issue_key, transition_id, fields=None, comment=None):
    payload = {"transition": {"id": str(transition_id)}}
    if fields:
        payload["fields"] = fields
    if comment:
        payload["update"] = {"comment": [{"add": {"body": comment}}]}
    url = f"{get_config('JIRA_URL', DEFAULT_JIRA_URL)}/rest/api/2/issue/{issue_key}/transitions"
    headers = {"Authorization": _jira_auth_header(), "Content-Type": "application/json; charset=UTF-8"}
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, context=_ssl_context()) as resp:
            return json.loads(resp.read().decode("utf-8")) if resp.read() else {}
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        print(f"ERROR: HTTP {e.code} - {e.reason}", file=sys.stderr)
        print(f"  Response: {err_body}", file=sys.stderr)
        sys.exit(1)


def add_comment(issue_key, body_text):
    payload = {"body": body_text}
    url = f"{get_config('JIRA_URL', DEFAULT_JIRA_URL)}/rest/api/2/issue/{issue_key}/comment"
    headers = {"Authorization": _jira_auth_header(), "Content-Type": "application/json; charset=UTF-8"}
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, context=_ssl_context()) as resp:
            return json.loads(resp.read().decode("utf-8")) if resp.read() else {}
    except urllib.error.HTTPError as e:
        print(f"WARN: Failed to add comment: HTTP {e.code}", file=sys.stderr)


def transition_issue(issue_key, direction="pass", dry_run=False):
    if dry_run:
        return True
    transitions = get_issue_transitions(issue_key)
    target_keywords = {
        "pass": ["\u901a\u8fc7", "\u5df2\u8bc4\u5ba1", "Done", "Resolved", "\u8bc4\u5ba1\u901a\u8fc7"],
        "fail": ["\u4e0d\u901a\u8fc7", "Rejected", "\u5f85\u4fee\u6539", "\u8bc4\u5ba1\u4e0d\u901a\u8fc7"],
    }
    for t in transitions.get("transitions", []):
        name_lower = t.get("name", "").lower()
        for kw in target_keywords.get(direction, []):
            if kw.lower() in name_lower:
                do_transition(issue_key, t.get("id"))
                return True
    print(f"  [WARN] No matching transition found (direction={direction})")
    return False


# ============================================================================
# Core Business Logic
# ============================================================================

def extract_gerrit_links(text):
    links = []
    for m in GERRIT_LINK_PATTERN.finditer(text):
        link = m.group(0).strip()
        if link.isdigit():
            links.append(link)
        elif link:
            links.append(link)
    return links


def build_se_comment(module, se_name):
    return f"\u9886\u57dfSE_{module}_\u4ee3\u7801review_OK_{se_name}"


def process_gerrit_link(change_id, module, se_name, dry_run=False):
    print(f"\n  {'='*50}")
    print(f"  Processing Gerrit Change: {change_id}")
    print(f"  {'='*50}")

    base_url, change_info, current_rev = find_change_site(change_id)
    if not base_url:
        print(f"  [ERROR] Change {change_id} not found in any Gerrit site")
        return False
    print(f"  Gerrit site: {base_url}")
    subject = change_info.get("subject", "?")
    status = change_info.get("status", "?")
    print(f"  Subject: {subject}")
    print(f"  Status: {status}")

    labels = get_labels(change_info)
    verified_ok = check_verified_plus_one(labels)
    print(f"  Verified +1: {'Yes' if verified_ok else 'No'}")

    commit_msg = get_commit_message(base_url, change_id)
    has_report = has_codereview_report(commit_msg)
    print(f"  codereview_Report: {'Found' if has_report else 'None'}")

    se_comment = build_se_comment(module, se_name)

    if dry_run:
        print(f"  [DRY RUN] Would: Code-Review +2, Comment, Checklist, Submit")
        return True

    print(f"  2a. Giving Code-Review +2 ...")
    post_review(base_url, change_id, current_rev, labels={"Code-Review": "+2"})
    print(f"      [OK] Code-Review +2 added")

    print(f"  2b. Adding SE comment: {se_comment}")
    post_review(base_url, change_id, current_rev, message=se_comment)
    print(f"      [OK] SE Comment added")

    if has_report:
        print(f"  3. Processing Confluence checklist ...")
        cl_status = do_confluence_checklist(commit_msg)
        status_map = {
            "completed": "[OK] checklist confirmed",
            "already_done": "[OK] checklist already done",
            "not_found": "no codereview_Report",
            "failed": "[WARN] checklist failed",
        }
        print(f"      {status_map.get(cl_status, cl_status)}")
    else:
        print(f"  3. Skipping Confluence checklist (no codereview_Report)")

    if status == "MERGED":
        print(f"  4. Already MERGED, skipping Submit")
    elif verified_ok:
        print(f"  4. Verified +1 ready, executing Submit ...")
        submit_result = submit_change(base_url, change_id)
        new_status = submit_result.get("status", "")
        if new_status == "MERGED":
            print(f"      [OK] Submit successful, change merged")
        else:
            print(f"      [WARN] Submit returned: {new_status}")
    else:
        print(f"  4. Verified not +1, skipping Submit")

    return True


# ============================================================================
# Main
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Domain SE Code Review - Independent version"
    )
    parser.add_argument("--issue", "-i", required=True, help="Jira issue key (e.g., ANDROID-12345)")
    parser.add_argument("--module", "-m", required=True, help="Module name (e.g., FRAMEWORK)")
    parser.add_argument("--user", "-u", required=True, help="SE username (uppercase)")
    parser.add_argument("--result", "-r", default="pass", choices=["pass", "fail"], help="Review result (default: pass)")
    parser.add_argument("--dry-run", "-n", action="store_true", help="Preview mode, no actual changes")
    args = parser.parse_args()

    issue_key = args.issue.upper()
    module = args.module.upper()
    se_name = args.user.upper()

    print("=" * 60)
    print(f"  Domain SE Code Review - {issue_key}")
    print(f"  Module: {module}  |  SE: {se_name}  |  Result: {args.result}")
    print(f"  Mode: {'Preview' if args.dry_run else 'Execute'}")
    print("=" * 60)

    print(f"\nStep 1: Fetching Jira Issue {issue_key} ...")
    try:
        issue = get_issue(issue_key)
    except Exception as e:
        print(f"[ERROR] Cannot fetch Jira issue: {e}")
        sys.exit(1)
    description = issue.get("fields", {}).get("description", "") or ""

    print(f"\nStep 2: Extracting Gerrit links from Jira description ...")
    gerrit_links = extract_gerrit_links(description)
    if not gerrit_links:
        print("  [WARN] No Gerrit links found in Jira description")
    else:
        print(f"  Found {len(gerrit_links)} Gerrit link(s):")
        for link in gerrit_links:
            print(f"    - {link}")

    all_ok = True
    for link in gerrit_links:
        if not process_gerrit_link(link, module, se_name, dry_run=args.dry_run):
            all_ok = False

    print(f"\nStep 4: Transitioning Jira Issue {issue_key} ...")
    direction = "pass" if args.result == "pass" else "fail"
    summary = f"\u9886\u57dfSE_{module}_\u4ee3\u7801review_{'OK' if args.result == 'pass' else 'NG'}_{se_name}"
    if gerrit_links:
        summary += f"\n\nReviewed {len(gerrit_links)} Gerrit Change(s):\n"
        for link in gerrit_links:
            summary += f"- {link}\n"

    if args.dry_run:
        print(f"  [DRY RUN] Would transition to: {direction}")
        print(f"  [DRY RUN] Would add comment: {summary[:80]}...")
    else:
        if gerrit_links:
            add_comment(issue_key, summary)
        transition_issue(issue_key, direction=direction)
        print(f"  [OK] Jira Issue {issue_key} transitioned")

    print(f"\n{'=' * 60}")
    print(f"  Done! Review result: {'Passed' if args.result == 'pass' else 'Failed'}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
