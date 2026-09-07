#!/usr/bin/env python3
"""拉取源页面 storage HTML 并保存为文件（复用 connector-proxy MCP 认证）。"""
import json, os, sys, requests

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE_PAGE_ID = "777891724"
OUT = os.path.join(SKILL_DIR, "tmp", "source_w34_storage.html")

def load_mcp_auth():
    raw = os.environ.get("CODEBUDDY_MCP_CONFIG", "")
    cfg = json.loads(raw)
    cp = cfg.get("mcpServers", {}).get("connector-proxy", {})
    url = cp.get("url", "http://127.0.0.1:56677/mcp")
    headers = cp.get("headers", {})
    return url, headers

url, headers = load_mcp_auth()
s = requests.Session()
sid = None

def post(payload):
    global sid
    h = dict(headers)
    if sid:
        h["Mcp-Session-Id"] = sid
    h.setdefault("Content-Type", "application/json")
    h.setdefault("Accept", "application/json, text/event-stream")
    r = s.post(url, headers=h, json=payload, timeout=120)
    r.raise_for_status()
    if not sid:
        sid = r.headers.get("Mcp-Session-Id")
    return r

post({"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"fetch-src","version":"1.0"}}})
post({"jsonrpc":"2.0","method":"notifications/initialized"})

r = post({"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"mcp-confluence_confluence_get_page","arguments":{"page_id":SOURCE_PAGE_ID,"convert_to_markdown":False,"include_metadata":False}}})

text = r.text
if text.startswith("event:"):
    text = text.split("\n",1)[1]
if text.startswith("data:"):
    text = text[5:].lstrip()
text = text.strip()
data = json.loads(text)
result = data.get("result", {})
content = result.get("content", [])
if content:
    inner = content[0].get("text","")
    # inner 可能是 JSON 字符串，也可能直接是 storage HTML
    try:
        inner = json.loads(inner)
    except Exception:
        pass
    if isinstance(inner, dict):
        # 可能结构是 {"content": {"value": "..."}}
        val = inner.get("content", {}).get("value") if isinstance(inner.get("content"), dict) else None
        if not val:
            val = inner.get("value")
        html_val = val
    else:
        html_val = inner
else:
    # 也可能 result 直接含 content value
    html_val = result.get("content", {}).get("value") if isinstance(result.get("content"), dict) else result.get("value")

if not html_val:
    # dump for debug
    with open(OUT + ".debug.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print("ERROR: no storage value; wrote debug json", file=sys.stderr)
    sys.exit(1)

with open(OUT, "w", encoding="utf-8") as f:
    f.write(html_val)
print(f"saved {len(html_val)} chars -> {OUT}")
