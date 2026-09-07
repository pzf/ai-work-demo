#!/usr/bin/env python3
"""通过 connector-proxy MCP 端点获取源页面完整 storage 内容并落盘。"""
import json, os, sys
import requests

TMP = os.path.dirname(os.path.abspath(__file__))

def load_mcp_auth():
    raw = os.environ.get("CODEBUDDY_MCP_CONFIG", "")
    if not raw:
        sys.exit("缺少 CODEBUDDY_MCP_CONFIG")
    cfg = json.loads(raw)
    cp = cfg.get("mcpServers", {}).get("connector-proxy", {})
    url = cp.get("url", "http://127.0.0.1:56677/mcp")
    headers = cp.get("headers", {})
    return url, headers

def main():
    mcp_url, headers = load_mcp_auth()
    s = requests.Session()
    session_id = None
    next_id = 1

    def post(payload, with_session=True):
        nonlocal session_id
        h = dict(headers)
        if with_session and session_id:
            h["Mcp-Session-Id"] = session_id
        h.setdefault("Content-Type", "application/json")
        h.setdefault("Accept", "application/json, text/event-stream")
        r = s.post(mcp_url, headers=h, json=payload, timeout=120)
        r.raise_for_status()
        if with_session and not session_id:
            sid = r.headers.get("Mcp-Session-Id")
            if sid:
                session_id = sid
        return r

    def parse_sse(r):
        text = r.content.decode("utf-8")
        if text.startswith("event:"):
            text = text.split("\n", 1)[1]
        if text.startswith("data:"):
            text = text[5:].lstrip()
        text = text.strip()
        return json.loads(text)

    post({"jsonrpc": "2.0", "id": next_id, "method": "initialize",
          "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                     "clientInfo": {"name": "wr-publish", "version": "1.0"}}})
    next_id += 1
    post({"jsonrpc": "2.0", "method": "notifications/initialized"})

    r = post({"jsonrpc": "2.0", "id": next_id, "method": "tools/call",
              "params": {"name": "mcp-confluence_confluence_get_page",
                         "arguments": {"page_id": "769721375", "convert_to_markdown": False,
                                       "include_metadata": False}}})
    next_id += 1
    result = parse_sse(r)
    content = result.get("result", {}).get("content", [])
    text = content[0].get("text", "") if content else ""
    data = json.loads(text)
    value = data.get("content", {}).get("value", "")
    if not value:
        print("ERROR: 未获取到 storage 内容", file=sys.stderr)
        print(text[:500], file=sys.stderr)
        sys.exit(1)
    out = os.path.join(TMP, "source_w42_storage.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(value)
    print(f"已保存 {len(value)} 字符 -> {out}")

if __name__ == "__main__":
    main()
