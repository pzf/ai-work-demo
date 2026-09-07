#!/usr/bin/env python3
"""两阶段创建新周报页面：先用骨架 create，再 update 为完整 storage 内容。"""
import os, sys, json, requests

def load_mcp_auth():
    raw = os.environ.get("CODEBUDDY_MCP_CONFIG", "")
    cfg = json.loads(raw)
    cp = cfg.get("mcpServers", {}).get("connector-proxy", {})
    url = cp.get("url", "http://127.0.0.1:56677/mcp")
    headers = cp.get("headers", {})
    return url, headers

class MCP:
    def __init__(self, mcp_url, headers):
        self.mcp_url = mcp_url
        self.headers = dict(headers)
        self.session = requests.Session()
        self.session_id = None
        self._next_id = 1
        self._post({"jsonrpc":"2.0","id":self._next_id,"method":"initialize",
                    "params":{"protocolVersion":"2024-11-05","capabilities":{},
                              "clientInfo":{"name":"report-pub","version":"1.0"}}})
        self._next_id += 1
        self._post({"jsonrpc":"2.0","method":"notifications/initialized"})

    def _post(self, payload):
        h = dict(self.headers)
        if self.session_id:
            h["Mcp-Session-Id"] = self.session_id
        h.setdefault("Content-Type", "application/json")
        h.setdefault("Accept", "application/json, text/event-stream")
        r = self.session.post(self.mcp_url, headers=h, json=payload, timeout=120)
        r.raise_for_status()
        if not self.session_id:
            sid = r.headers.get("Mcp-Session-Id")
            if sid:
                self.session_id = sid
        return r

    def _parse(self, resp):
        text = resp.content.decode("utf-8", errors="replace")
        if text.startswith("event:"):
            text = text.split("\n", 1)[1]
        if text.startswith("data:"):
            text = text[5:].lstrip()
        text = text.strip()
        return json.loads(text)

    def call(self, tool, arguments):
        for attempt in range(3):
            r = self._post({"jsonrpc":"2.0","id":self._next_id,"method":"tools/call",
                            "params":{"name":tool,"arguments":arguments}})
            self._next_id += 1
            result = self._parse(r)
            if result.get("error"):
                raise RuntimeError(f"MCP error: {result['error']}")
            content = result.get("result", {}).get("content", [])
            text = content[0].get("text", "")
            if content[0].get("isError"):
                raise RuntimeError(f"工具失败: {text}")
            if "reconnect" in text:
                continue
            return text
        raise RuntimeError("重试后仍失败")

def main():
    space_key = sys.argv[1]
    title = sys.argv[2]
    parent_id = sys.argv[3]
    content_file = sys.argv[4]

    url, headers = load_mcp_auth()
    c = MCP(url, headers)

    # 阶段 A1：创建骨架
    print("创建骨架页面...")
    r1 = c.call("mcp-confluence_confluence_create_page", {
        "space_key": space_key,
        "title": title,
        "content": "<p>placeholder</p>",
        "parent_id": parent_id,
        "content_format": "storage",
    })
    print("create 返回:", r1[:300])
    data1 = json.loads(r1)
    # 提取新页面 id
    page_id = None
    if isinstance(data1, dict):
        page_id = data1.get("id") or data1.get("page_id")
        if not page_id and "page" in data1:
            page_id = data1["page"].get("id")
    if not page_id:
        # 尝试从字符串提取
        import re
        m = re.search(r'"id"\s*:\s*"?(\d+)', r1)
        if m:
            page_id = m.group(1)
    if not page_id:
        sys.exit(f"无法从 create 返回中提取 page_id: {r1[:500]}")
    print("新页面 id:", page_id)

    # 阶段 A2：更新为完整内容
    full = open(content_file, encoding="utf-8").read()
    print("更新为完整内容...")
    r2 = c.call("mcp-confluence_confluence_update_page", {
        "page_id": page_id,
        "title": title,
        "content": full,
        "content_format": "storage",
    })
    print("update 返回:", r2[:300])
    print(f"PAGE_ID={page_id}")

if __name__ == "__main__":
    main()
