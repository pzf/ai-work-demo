#!/usr/bin/env python3
"""通过 connector-proxy MCP 端点拉取 W32 源页面 storage HTML 并保存到文件。"""
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
    resp = session.post(url, headers=h, json=payload, timeout=120)
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
    text = text.strip()
    return json.loads(text)

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
    text = content[0].get("text", "")
    return json.loads(text)

# initialize
_post({"jsonrpc": "2.0", "id": _next_id, "method": "initialize",
       "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                  "clientInfo": {"name": "publish", "version": "1.0"}}})
_next_id += 1
_post({"jsonrpc": "2.0", "method": "notifications/initialized"})

# 拉取源页面（include_metadata=False 才返回 content）
data = _call("mcp-confluence_confluence_get_page",
             {"page_id": "768619083", "include_metadata": False, "convert_to_markdown": False})

# 提取 HTML
html = None
if isinstance(data, dict):
    content = data.get("content")
    if isinstance(content, dict):
        # content = {"value": "...", "format": "storage"}
        html = content.get("value")
    elif isinstance(content, str):
        html = content
    elif isinstance(content, list):
        for c in content:
            if isinstance(c, dict) and c.get("type") == "text":
                html = c.get("text")
                break
    if not html and isinstance(data.get("value"), str):
        html = data["value"]

if not html:
    raise RuntimeError(f"未能从返回中提取 HTML: {json.dumps(data)[:500]}")

# connector-proxy 返回的中文是 UTF-8 字节被按 Latin-1 解码的 mojibake，
# 需反转恢复正确中文（每个 char 编码回 latin-1 字节，再按 utf-8 解码）
def fix_mojibake(s):
    try:
        fixed = s.encode('latin-1').decode('utf-8')
        # 校验修复有效：若失败会抛异常，则原样返回
        return fixed
    except (UnicodeEncodeError, UnicodeDecodeError):
        return s

html = fix_mojibake(html)

out = os.path.join(os.path.dirname(__file__), "reports", "source_w32_storage.html")
os.makedirs(os.path.dirname(out), exist_ok=True)
with open(out, "w", encoding="utf-8") as f:
    f.write(html)
print(f"saved {len(html)} bytes -> {out}")
