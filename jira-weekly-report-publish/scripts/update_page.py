#!/usr/bin/env python3
"""将替换+清空后的 W33 页面 HTML 写回 Confluence（confluence_update_page）。"""
import json, os, requests

raw = os.environ.get("CODEBUDDY_MCP_CONFIG", "")
cfg = json.loads(raw)
cp = cfg["mcpServers"]["connector-proxy"]
url = cp["url"]
headers = dict(cp["headers"])

session = requests.Session()
session_id = None
_next_id = 1

def _post(payload, with_session=True):
    global session_id
    h = dict(headers)
    if with_session and session_id:
        h["Mcp-Session-Id"] = session_id
    h.setdefault("Content-Type", "application/json")
    h.setdefault("Accept", "application/json, text/event-stream")
    resp = session.post(url, headers=h, json=payload, timeout=180)
    resp.raise_for_status()
    if with_session and not session_id:
        sid = resp.headers.get("Mcp-Session-Id")
        if sid:
            session_id = sid
    return resp

def _parse_sse(resp):
    text = resp.text
    if text.startswith("event:"):
        text = text.split("\n", 1)[1]
    if text.startswith("data:"):
        text = text[5:].lstrip()
    return json.loads(text.strip())

def _call(tool, arguments):
    global _next_id
    r = _post({
        "jsonrpc": "2.0", "id": _next_id, "method": "tools/call",
        "params": {"name": tool, "arguments": arguments},
    })
    _next_id += 1
    result = _parse_sse(r)
    if result.get("error"):
        raise RuntimeError(f"MCP error: {result['error']}")
    content = result.get("result", {}).get("content", [])
    if not content:
        raise RuntimeError("MCP 返回空 content")
    text = content[0].get("text", "")
    if content[0].get("isError"):
        raise RuntimeError(f"工具调用失败：{text}")
    return json.loads(text)

# initialize
_post({"jsonrpc": "2.0", "id": _next_id, "method": "initialize",
       "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                  "clientInfo": {"name": "publish", "version": "1.0"}}})
_next_id += 1
_post({"jsonrpc": "2.0", "method": "notifications/initialized"})

# 读取更新后的 HTML
html = open(os.path.join(os.path.dirname(__file__), "reports", "updated_w33_page.html"),
            encoding="utf-8").read()

# 更新 W33 页面
result = _call("mcp-confluence_confluence_update_page", {
    "page_id": "771326625",
    "title": "框架开发二组【 2026W33】",
    "content": html,
    "content_format": "storage",
})

print(json.dumps(result, ensure_ascii=False)[:500])
