#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
domain-se-review - Independent SE review script

Workflow:
1. Parse Jira issue, extract Gerrit links from description
2. For each Gerrit Change: Code-Review +2, SE comment, Confluence checklist
3. Transition Jira issue status to "评审通过"

Config priority: env vars > ~/.gerrit_env > config.json
"""

import argparse
import base64
import json
import os
import re
import shutil
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request

# Ensure no .pyc caching by removing __pycache__ directories on startup
_script_dir = os.path.dirname(os.path.abspath(__file__))
_pycache_dir = os.path.join(_script_dir, "__pycache__")
if os.path.isdir(_pycache_dir):
    shutil.rmtree(_pycache_dir, ignore_errors=True)
# Also check parent directory
_parent_pycache = os.path.join(os.path.dirname(_script_dir), "__pycache__")
if os.path.isdir(_parent_pycache):
    shutil.rmtree(_parent_pycache, ignore_errors=True)

DEFAULT_GERRIT_URL = "http://sz.gerrit.tclcom.com:8080"
DEFAULT_GERRIT_SITES = [
    "http://sz.gerrit.tclcom.com:8080",
    "http://hz.gerrit.tclcom.com:8081",
]
CONFLUENCE_BASE = "https://confluence.tclking.com/rest/api"
DEFAULT_JIRA_URL = "https://jira.tcl.com"

# SSL context for legacy TLS support
def _ssl_context():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    ctx.options |= 0x4  # OP_LEGACY_SERVER_CONNECT
    return ctx

GERRIT_LINK_PATTERN = re.compile(
    r'(https?://[^\s"\'<>]+/c/[^\s"\'<>]+/\+/\d+|'
    r'https?://[^\s"\'<>]+/\d+)',
    re.IGNORECASE
)

CONFLUENCE_LINK_PATTERN = re.compile(
    r'https?://confluence\.tclking\.com/pages/viewpage\.action\?pageId=(\d+)',
    re.IGNORECASE
)

# Section header markers inside Confluence review-detail pages
REVIEW_SECTION_PATTERN = re.compile(
    r'<h2>\s*未Review列表\s*</h2>(.*?)(?:<h2>|</body>|$)',
    re.IGNORECASE | re.DOTALL
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
    """Get config value. Priority: ~/.gerrit_env > config.json > default."""
    _gerrit_env_loaded()
    # ~/.gerrit_env first
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
    # config.json fallback
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
    cfg_sites = get_config("GERRIT_SITES")
    if cfg_sites:
        return cfg_sites if isinstance(cfg_sites, list) else [s.strip() for s in cfg_sites.split(",")]
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

class GerritError(Exception):
    """Exception raised for Gerrit API errors."""
    def __init__(self, code, reason, url=None, response=None):
        self.code = code
        self.reason = reason
        self.url = url
        self.response = response
        super().__init__(f"HTTP {code} - {reason}")


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
        raise GerritError(e.code, e.reason, url=url, response=err_body)
    except urllib.error.URLError as e:
        raise GerritError(0, str(e.reason), url=url)


def _extract_change_number(change_id):
    """Extract numeric change ID from a Gerrit URL or return the original if already numeric."""
    if change_id.isdigit():
        return change_id
    if "://" in change_id:
        match = re.search(r'/(\d+)(?:\?|$)', change_id)
        if match:
            return match.group(1)
    return change_id


def find_change_site(change_id):
    sites = get_gerrit_sites()
    numeric_id = _extract_change_number(change_id)
    for site in sites:
        base_url = site.rstrip("/")
        try:
            encoded_id = urllib.parse.quote(numeric_id, safe="")
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


def check_code_review_plus_two_exists(labels):
    """Check if Code-Review +2 already exists from any user."""
    cr = labels.get("Code-Review", {})
    for _user, value in cr.get("all_votes", {}).items():
        if value >= 2:
            return True
    return False


def check_se_comment_exists(base_url, change_id, se_comment):
    """Check if the same SE comment already exists on this change.

    Uses /detail endpoint to check the messages array (which stores review
    comments), not the /comments endpoint (which only stores inline comments).
    """
    encoded_id = urllib.parse.quote(change_id, safe="")
    url = f"{base_url}/a/changes/{encoded_id}/detail"
    try:
        data = gerrit_request(url, base_url=base_url)
        messages = data.get("messages", [])
        for msg_obj in messages:
            msg = msg_obj.get("message", "")
            if se_comment in msg:
                return True
    except Exception:
        pass
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
    """Process Confluence checklist. Returns: completed / already_done / not_found / failed / skipped"""
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
            data = resp.read()
            # Jira returns 204 No Content on successful transitions with empty body
            # Treat any successful status (2xx) as success regardless of body
            return resp.status < 300
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
            return True
    except urllib.error.HTTPError as e:
        print(f"WARN: Failed to add comment: HTTP {e.code}", file=sys.stderr)
        return False


def transition_issue(issue_key, target_status_id=None, dry_run=False, comment=None):
    """Transition Jira issue to target status by ID, or fallback to keyword matching."""
    if dry_run:
        return True
    transitions = get_issue_transitions(issue_key)

    # Primary: match by target status ID
    if target_status_id:
        for t in transitions.get("transitions", []):
            to_status = t.get("to", {})
            if to_status.get("id") == str(target_status_id):
                result = do_transition(issue_key, t.get("id"), comment=comment)
                if result:
                    return True
                print(f"  [WARN] Transition to status {target_status_id} ('{to_status.get('name')}') failed")
                return False
        print(f"  [WARN] No transition found to status ID '{target_status_id}'")
        available = [f"{t.get('id')}->{t.get('to',{}).get('id')}({t.get('to',{}).get('name')})" for t in transitions.get("transitions", [])]
        print(f"  Available transitions: {available if available else 'none'}")
        return False

    # Fallback: match by keywords (for backwards compatibility)
    target_keywords = ["\u8bc4\u5ba1\u901a\u8fc7", "\u5df2\u8bc4\u5ba1", "Done", "Resolved"]
    for t in transitions.get("transitions", []):
        name_lower = t.get("name", "").lower()
        for kw in target_keywords:
            if kw.lower() in name_lower:
                result = do_transition(issue_key, t.get("id"), comment=comment)
                if result:
                    return True
                print(f"  [WARN] Transition '{t.get('name')}' failed")
                return False
    print(f"  [WARN] No matching transition found for '评审通过'")
    available = [t.get("name") for t in transitions.get("transitions", [])]
    print(f"  Available transitions: {available if available else 'none'}")
    return False


# ============================================================================
# Core Business Logic
# ============================================================================

def extract_gerrit_links(text):
    links = []
    seen = set()
    for m in GERRIT_LINK_PATTERN.finditer(text):
        link = m.group(0).strip()
        if link in seen:
            continue
        seen.add(link)
        if link:
            links.append(link)
    return links


def extract_confluence_page_ids(text):
    """Extract Confluence page IDs from text (deduplicated, order-preserved)."""
    ids = []
    seen = set()
    for m in CONFLUENCE_LINK_PATTERN.finditer(text or ""):
        pid = m.group(1)
        if pid in seen:
            continue
        seen.add(pid)
        ids.append(pid)
    return ids


def extract_unreviewed_gerrit_links_from_confluence(confluence_url):
    """Fetch a Confluence review-detail page and extract Gerrit links from the
    '未Review列表' (not-yet-reviewed) table only.

    Returns (links, error): links is a list of Gerrit URLs, error is a message
    string (or None on success). Never raises; on failure returns ([], error).
    """
    page_id = None
    m = CONFLUENCE_LINK_PATTERN.search(confluence_url or "")
    if m:
        page_id = m.group(1)
    else:
        # Fallback: accept a bare pageId number
        m2 = re.fullmatch(r"\d+", (confluence_url or "").strip())
        if m2:
            page_id = m2.group(0)

    if not page_id:
        return [], f"Cannot extract pageId from Confluence URL: {confluence_url}"

    print(f"  Fetching Confluence page {page_id} ...")
    page = get_confluence_page(page_id)
    if page is None:
        return [], f"Cannot fetch Confluence page {page_id} (CONFLUENCE_KEY not set or invalid)"

    body = page.get("body", {}).get("storage", {}).get("value", "")
    if not body:
        return [], f"Confluence page {page_id} has empty body"

    # Isolate the '未Review列表' section only (stop at the next <h2>).
    section_m = REVIEW_SECTION_PATTERN.search(body)
    if not section_m:
        return [], f"No '未Review列表' section found on Confluence page {page_id}"

    section = section_m.group(1)
    links = []
    seen = set()
    for m2 in GERRIT_LINK_PATTERN.finditer(section):
        link = m2.group(0).strip()
        if link in seen:
            continue
        seen.add(link)
        if link:
            links.append(link)

    if not links:
        return [], f"No Gerrit links found in '未Review列表' section on Confluence page {page_id}"
    return links, None


def build_se_comment(module, se_name):
    return f"\u9886\u57dfSE_{module}_\u4ee3\u7801review_OK_{se_name}"


def process_gerrit_link(change_id, module, se_name, dry_run=False):
    """Process a single Gerrit change. Returns dict with results."""
    result = {
        "change_id": change_id,
        "code_review": "skipped",
        "se_comment": "skipped",
        "checklist": "skipped",
        "success": True,
        "error": None,
    }

    print(f"\n  {'='*50}")
    print(f"  Processing Gerrit Change: {change_id}")
    print(f"  {'='*50}")

    base_url, change_info, current_rev = find_change_site(change_id)
    if not base_url:
        print(f"  [ERROR] Change {change_id} not found in any Gerrit site")
        result["success"] = False
        result["error"] = "Change not found"
        return result

    numeric_id = _extract_change_number(change_id)
    print(f"  Gerrit site: {base_url}")
    subject = change_info.get("subject", "?")
    status = change_info.get("status", "?")
    print(f"  Subject: {subject}")
    print(f"  Status: {status}")

    labels = get_labels(change_info)
    commit_msg = get_commit_message(base_url, numeric_id)
    has_report = has_codereview_report(commit_msg)
    print(f"  codereview_Report: {'Found' if has_report else 'None'}")

    se_comment = build_se_comment(module, se_name)

    # ---- Check: Code-Review +2 already exists? ----
    if check_code_review_plus_two_exists(labels):
        print(f"  2a. Code-Review +2 already exists, skipping.")
        result["code_review"] = "skipped(已有+2)"
    elif dry_run:
        print(f"  2a. [DRY RUN] Would give Code-Review +2")
        result["code_review"] = "dry_run"
    else:
        print(f"  2a. Giving Code-Review +2 ...")
        try:
            post_review(base_url, numeric_id, current_rev, labels={"Code-Review": "+2"})
            print(f"      [OK] Code-Review +2 added")
            result["code_review"] = "added"
        except Exception as e:
            print(f"      [ERROR] Code-Review +2 failed: {e}")
            result["code_review"] = "failed"
            result["success"] = False
            result["error"] = str(e)

    # ---- Check: SE Comment already exists? ----
    if check_se_comment_exists(base_url, numeric_id, se_comment):
        print(f"  2b. SE Comment already exists ({se_comment}), skipping.")
        result["se_comment"] = "skipped(已有)"
    elif dry_run:
        print(f"  2b. [DRY RUN] Would add SE comment: {se_comment}")
        result["se_comment"] = "dry_run"
    else:
        print(f"  2b. Adding SE comment: {se_comment}")
        try:
            post_review(base_url, numeric_id, current_rev, message=se_comment)
            print(f"      [OK] SE Comment added")
            result["se_comment"] = "added"
        except Exception as e:
            print(f"      [ERROR] SE Comment failed: {e}")
            result["se_comment"] = "failed"
            result["success"] = False
            result["error"] = str(e)

    # ---- Confluence Checklist ----
    if not has_report:
        print(f"  3. Skipping Confluence checklist (no codereview_Report)")
        result["checklist"] = "not_found"
    elif dry_run:
        print(f"  3. [DRY RUN] Would process Confluence checklist")
        result["checklist"] = "dry_run"
    else:
        print(f"  3. Processing Confluence checklist ...")
        cl_status = do_confluence_checklist(commit_msg)
        status_map = {
            "completed": "[OK] checklist confirmed",
            "already_done": "[OK] checklist already done",
            "not_found": "no codereview_Report",
            "failed": "[WARN] checklist failed",
        }
        print(f"      {status_map.get(cl_status, cl_status)}")
        result["checklist"] = cl_status

    return result


# ============================================================================
# Main
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Domain SE Code Review - Independent version"
    )
    parser.add_argument("--issue", "-i", required=True, help="Jira issue key (e.g., ANDROID-12345)")
    parser.add_argument("--module", "-m", help="Module name (e.g., FRAMEWORK). If not set, uses DEFAULT_MODULE from config.json")
    parser.add_argument("--user", "-u", required=True, help="SE username (uppercase)")
    parser.add_argument("--target-status-id", "-t", help="Target Jira status ID to transition to (e.g., 11523 for '通过')")
    parser.add_argument("--dry-run", "-n", action="store_true", help="Preview mode, no actual changes")
    args = parser.parse_args()

    issue_key = args.issue.upper()
    se_name = args.user.upper()

    # Module: CLI args > config.json DEFAULT_MODULE
    module = args.module.upper() if args.module else get_config("DEFAULT_MODULE", "").upper()
    if not module:
        print("ERROR: --module not specified and DEFAULT_MODULE not found in config.json", file=sys.stderr)
        sys.exit(1)

    # Target status ID: CLI args > config.json TARGET_STATUS_ID
    target_status_id = args.target_status_id or get_config("TARGET_STATUS_ID", "")
    if not target_status_id:
        print("WARN: --target-status-id not specified and TARGET_STATUS_ID not found in config.json, using keyword matching", file=sys.stderr)

    print("=" * 60)
    print(f"  Domain SE Code Review - {issue_key}")
    print(f"  Module: {module}  |  SE: {se_name}")
    print(f"  Target Status ID: {target_status_id or '(keyword matching)'}")
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
    link_source = "Jira description"

    # Fallback: no direct Gerrit link, but the description points to a
    # Confluence review-detail page -> fetch it and take '未Review列表' links.
    if not gerrit_links:
        cf_page_ids = extract_confluence_page_ids(description)
        if cf_page_ids:
            cf_url = f"https://confluence.tclking.com/pages/viewpage.action?pageId={cf_page_ids[0]}"
            print(f"  No Gerrit links in description; trying Confluence page {cf_url} ...")
            gerrit_links, cf_err = extract_unreviewed_gerrit_links_from_confluence(cf_url)
            if cf_err:
                print(f"  [WARN] {cf_err}")
            else:
                link_source = f"Confluence page (未Review列表, {cf_url})"

    if not gerrit_links:
        print("  [WARN] No Gerrit links found in Jira description")
    else:
        print(f"  Found {len(gerrit_links)} Gerrit link(s) from {link_source}:")
        for link in gerrit_links:
            print(f"    - {link}")

    # Process each Gerrit and collect results
    all_results = []
    for link in gerrit_links:
        result = process_gerrit_link(link, module, se_name, dry_run=args.dry_run)
        all_results.append(result)

    # Build summary comment for Jira
    summary_lines = [f"\u9886\u57dfSE_{module}_\u4ee3\u7801review_OK_{se_name}"]
    if gerrit_links:
        summary_lines.append(f"\n\nReviewed {len(gerrit_links)} Gerrit Change(s):")
        for link in gerrit_links:
            summary_lines.append(f"- {link}")

    summary = "\n".join(summary_lines)

    # Transition Jira
    print(f"\nStep 3: Transitioning Jira Issue {issue_key} ...")
    jira_transitioned = False
    if args.dry_run:
        target_desc = f"status ID {target_status_id}" if target_status_id else "'评审通过'"
        print(f"  [DRY RUN] Would transition to {target_desc}")
        print(f"  [DRY RUN] Would add comment: {summary[:80]}...")
    else:
        if gerrit_links:
            add_comment(issue_key, summary)
        jira_transitioned = transition_issue(issue_key, target_status_id=target_status_id, comment=summary)

    # Print summary table
    print(f"\n{'=' * 70}")
    print(f"  评审汇总 - Jira: {issue_key}")
    print(f"{'=' * 70}")
    if gerrit_links:
        print(f"{'Change':<12} {'Code-Review':<18} {'SE Comment':<18} {'Checklist':<15}")
        print(f"{'-'*12} {'-'*18} {'-'*18} {'-'*15}")
        for r in all_results:
            cr = r["code_review"]
            sc = r["se_comment"]
            cl = r["checklist"]
            change_short = r["change_id"].split("/")[-1] if "/" in r["change_id"] else r["change_id"]
            print(f"{change_short:<12} {cr:<18} {sc:<18} {cl:<15}")
    else:
        print("  (无 Gerrit 链接)")
    print(f"\nJira 流转: {'[预览]' if args.dry_run else ('[OK] 已流转至 评审通过' if jira_transitioned else '[FAIL] 流转失败')}")
    print(f"{'=' * 70}")

    # Exit with error if any Gerrit failed
    if any(not r["success"] for r in all_results):
        sys.exit(1)


if __name__ == "__main__":
    main()
