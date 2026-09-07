#!/usr/bin/env python3
"""更新周报页面：将 updated_page.html 的完整 storage 内容写回新页面。"""
import json, os, sys
import requests

TMP = os.path.dirname(os.path.abspath(__file__))
PAGE_ID = "769751851"
PAGE_TITLE = "框架开发二组【 2026W43】"

def load_mcp_auth():
    cfg = json.loads(os.environ.get("CODEBUDDY_MCP_CONFIG", "{}"))
    cp = cfg.get("mcpServers", {}).get("connector-proxy", {})
    url = cp.get("url", "http://127.0.0.1:56677/mcp")
    headers = cp.get("headers", {})
    return url, headers

def main():
    mcp_url, headers = load_mcp_auth()
    content = open(os.path.join(TMP, "updated_page.html"), encoding="utf-8").read()

    s = requests.Session()
    sid = None
    nid = 1

    def post(payload, ws=True):
        nonlocal sid
        h = dict(headers)
        if ws and sid:
            h["Mcp-Session-Id"] = sid
        h.setdefault("Content-Type", "application/json")
        h.setdefault("Accept", "application/json, text/event-stream")
        r = s.post(mcp_url, headers=h, json=payload, timeout=180)
        r.raise_for_status()
        if ws and not sid:
            s2 = r.headers.get("Mcp-Session-Id")
            if s2:
                sid = s2
        return r

    def parse_sse(r):
        text = r.content.decode("utf-8")
        if text.startswith("event:"):
            text = text.split("\n", 1)[1]
        if text.startswith("data:"):
            text = text[5:].lstrip()
        return json.loads(text.strip())

    post({"jsonrpc": "2.0", "id": nid, "method": "initialize",
          "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                     "clientInfo": {"name": "wr-publish", "version": "1.0"}}}); nid += 1
    post({"jsonrpc": "2.0", "method": "notifications/initialized"})

    args = {
        "page_id": PAGE_ID,
        "title": PAGE_TITLE,
        "content": content,
        "content_format": "storage",
        "version_comment": "发布 W43 周报：填充 Jira 数据 + 清空本周进展",
    }
    r = post({"jsonrpc": "2.0", "id": nid, "method": "tools/call",
              "params": {"name": "mcp-confluence_confluence_update_page", "arguments": args}})
    nid += 1
    result = parse_sse(r)
    err = result.get("error")
    if err:
        print("ERROR:", json.dumps(err, ensure_ascii=False)[:500], file=sys.stderr)
        sys.exit(1)
    text = result.get("result", {}).get("content", [{}])[0].get("text", "")
    print("更新返回:", text[:300])

if __name__ == "__main__":
    main()
