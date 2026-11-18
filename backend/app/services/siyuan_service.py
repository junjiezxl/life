import asyncio
import json
import os
from typing import Any, Dict, List, Optional, Tuple

import httpx


SAMPLE_SIYUAN_NOTES: List[Dict[str, str]] = [
    {
        "id": "demo-20261118-01",
        "title": "本周工作与生活待办",
        "path": "/日常规划/本周待办",
        "updated": "2026-11-18 08:30",
        "content": """# 本周重点待办
- [ ] 明天下午4点前提交 Q4 技术架构评审 PPT #工作 #重要
- [ ] 周五晚上8点预约洗牙，记得带上医保卡 #健康
- [x] 已完成：周一团队周会纪要整理
- [ ] 下周一提醒续费家里宽带套餐 159元 #财务""",
    },
    {
        "id": "demo-20261118-02",
        "title": "11月财务与报销备忘",
        "path": "/个人财务/报销与账单",
        "updated": "2026-11-17 21:15",
        "content": """## 待处理账目
TODO: 11月25日前把出差高铁票和住宿发票发给财务报销（共1280元）
- [ ] 11月20日还招商银行信用卡账单 3420元
提醒：每月1号检查云盘和 AI 工具订阅是否需要继续保留 198元""",
    },
]


class McpStdioClient:
    """Minimal async JSON-RPC 2.0 client over stdio for local MCP servers."""

    def __init__(self, command: str, args: List[str], env: Dict[str, str], timeout_s: float = 15.0):
        self.command = command
        self.args = args
        self.env = env
        self.timeout_s = timeout_s
        self.proc: Optional[asyncio.subprocess.Process] = None
        self._req_id = 0

    async def __aenter__(self) -> "McpStdioClient":
        merged_env = os.environ.copy()
        for k, v in self.env.items():
            if v is not None and str(v) != "":
                merged_env[str(k)] = str(v)
        # Ensure common homebrew / nvm paths are reachable from FastAPI process
        extra_paths = [
            "/opt/homebrew/bin",
            "/usr/local/bin",
            os.path.expanduser("~/.nvm/versions/node/v22.23.1/bin"),
            os.path.expanduser("~/.nvm/versions/node/v20.20.2/bin"),
        ]
        merged_env["PATH"] = ":".join(extra_paths + [merged_env.get("PATH", "")])

        self.proc = await asyncio.create_subprocess_exec(
            self.command,
            *self.args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=merged_env,
        )
        await self._initialize()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self.proc and self.proc.returncode is None:
            try:
                self.proc.terminate()
                await asyncio.wait_for(self.proc.wait(), timeout=2.0)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass

    async def _send(self, payload: Dict[str, Any]) -> None:
        assert self.proc and self.proc.stdin
        raw = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
        self.proc.stdin.write(raw)
        await self.proc.stdin.drain()

    async def _request(self, method: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        assert self.proc and self.proc.stdout
        self._req_id += 1
        req_id = self._req_id
        payload: Dict[str, Any] = {"jsonrpc": "2.0", "id": req_id, "method": method}
        if params is not None:
            payload["params"] = params
        await self._send(payload)

        deadline = asyncio.get_event_loop().time() + self.timeout_s
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                raise TimeoutError(f"MCP stdio request '{method}' timed out after {self.timeout_s}s")
            line_bytes = await asyncio.wait_for(self.proc.stdout.readline(), timeout=remaining)
            if not line_bytes:
                stderr_text = ""
                if self.proc.stderr:
                    try:
                        stderr_bytes = await asyncio.wait_for(self.proc.stderr.read(), timeout=0.5)
                        stderr_text = stderr_bytes.decode("utf-8", errors="ignore").strip()
                    except Exception:
                        pass
                raise RuntimeError(f"MCP server exited before responding ({method}). {stderr_text}")
            line = line_bytes.decode("utf-8", errors="ignore").strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if msg.get("id") == req_id:
                if "error" in msg:
                    raise RuntimeError(f"MCP error: {msg['error']}")
                return msg.get("result") or {}

    async def _initialize(self) -> Dict[str, Any]:
        result = await self._request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "life-hub", "version": "1.0.0"},
            },
        )
        await self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return result

    async def list_tools(self) -> List[Dict[str, Any]]:
        result = await self._request("tools/list", {})
        return result.get("tools") or []

    async def call_tool(self, name: str, arguments: Dict[str, Any]) -> Any:
        return await self._request("tools/call", {"name": name, "arguments": arguments})


async def _mcp_http_rpc(url: str, method: str, params: Optional[Dict[str, Any]] = None, req_id: int = 1) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"jsonrpc": "2.0", "id": req_id, "method": method}
    if params is not None:
        payload["params"] = params
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        ctype = resp.headers.get("content-type", "")
        if "text/event-stream" in ctype:
            for line in resp.text.splitlines():
                if line.startswith("data:"):
                    data = json.loads(line[5:].strip())
                    if data.get("id") == req_id:
                        if "error" in data:
                            raise RuntimeError(str(data["error"]))
                        return data.get("result") or {}
            return {}
        data = resp.json()
        if "error" in data:
            raise RuntimeError(str(data["error"]))
        return data.get("result") or {}


def _parse_json_setting(raw: str, default: Any) -> Any:
    try:
        return json.loads(raw) if raw else default
    except json.JSONDecodeError:
        return default


def _normalize_siyuan_blocks(raw_payload: Any) -> List[Dict[str, str]]:
    """Best-effort normalization of SiYuan SQL / MCP tool output into NoteSnippet dicts."""
    items: List[Any] = []
    if isinstance(raw_payload, dict):
        if isinstance(raw_payload.get("content"), list):
            # MCP tools/call response: {"content": [{"type": "text", "text": "..."}]}
            for part in raw_payload["content"]:
                if isinstance(part, dict) and part.get("type") == "text":
                    txt = part.get("text", "")
                    try:
                        parsed = json.loads(txt)
                        return _normalize_siyuan_blocks(parsed)
                    except json.JSONDecodeError:
                        if txt.strip():
                            items.append({"id": "mcp-text", "title": "思源 MCP 返回内容", "path": "/MCP", "content": txt})
        elif isinstance(raw_payload.get("data"), list):
            items = raw_payload["data"]
        elif isinstance(raw_payload.get("blocks"), list):
            items = raw_payload["blocks"]
        elif isinstance(raw_payload.get("results"), list):
            items = raw_payload["results"]
    elif isinstance(raw_payload, list):
        items = raw_payload

    grouped: Dict[str, Dict[str, str]] = {}
    for row in items:
        if not isinstance(row, dict):
            continue
        doc_id = str(row.get("root_id") or row.get("id") or "")
        hpath = str(row.get("hpath") or row.get("path") or row.get("title") or "/思源笔记")
        title = str(row.get("title") or (hpath.strip("/").split("/")[-1] if hpath else "未命名笔记"))
        text_chunk = str(row.get("markdown") or row.get("content") or "").strip()
        updated = str(row.get("updated") or "")
        if not text_chunk:
            continue
        key = hpath or doc_id or title
        if key not in grouped:
            grouped[key] = {
                "id": doc_id,
                "title": title,
                "path": hpath,
                "content": text_chunk,
                "updated": updated,
            }
        else:
            if text_chunk not in grouped[key]["content"]:
                grouped[key]["content"] += "\n" + text_chunk
    return list(grouped.values())


async def test_siyuan_connection(settings: Dict[str, str]) -> Dict[str, Any]:
    mode = settings.get("siyuan_mode", "mcp_stdio")
    if mode == "siyuan_api":
        api_url = (settings.get("siyuan_api_url") or "http://127.0.0.1:6806").rstrip("/")
        token = (settings.get("siyuan_token") or "").strip()
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Token {token}"
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.post(f"{api_url}/api/notebook/lsNotebooks", headers=headers, json={})
                resp.raise_for_status()
                body = resp.json()
                if body.get("code", 0) != 0:
                    return {"ok": False, "mode": mode, "message": f"思源 API 返回错误: {body.get('msg')}", "tools": []}
                notebooks = body.get("data", {}).get("notebooks", [])
                return {
                    "ok": True,
                    "mode": mode,
                    "message": f"已连接到本地思源内核，共发现 {len(notebooks)} 个笔记本。",
                    "tools": [{"name": "api/query/sql", "description": "SiYuan SQL 查询接口"}],
                    "notebooks": [n.get("name") for n in notebooks],
                }
        except Exception as exc:
            return {"ok": False, "mode": mode, "message": f"无法连接本地思源内核 ({api_url}): {exc}", "tools": []}

    if mode == "mcp_http":
        mcp_url = (settings.get("mcp_url") or "").strip()
        try:
            await _mcp_http_rpc(
                mcp_url,
                "initialize",
                {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "life-hub", "version": "1.0.0"}},
                req_id=1,
            )
            res = await _mcp_http_rpc(mcp_url, "tools/list", {}, req_id=2)
            tools = res.get("tools") or []
            return {
                "ok": True,
                "mode": mode,
                "message": f"MCP HTTP 连接成功，发现 {len(tools)} 个可用工具。",
                "tools": [{"name": t.get("name"), "description": t.get("description", "")} for t in tools],
            }
        except Exception as exc:
            return {"ok": False, "mode": mode, "message": f"MCP HTTP 连接失败 ({mcp_url}): {exc}", "tools": []}

    # Default: mcp_stdio
    cmd = (settings.get("mcp_command") or "npx").strip()
    args = _parse_json_setting(settings.get("mcp_args_json", ""), ["-y", "siyuan-mcp@latest"])
    env = _parse_json_setting(settings.get("mcp_env_json", ""), {})
    try:
        async with McpStdioClient(cmd, args, env, timeout_s=15.0) as client:
            tools = await client.list_tools()
            return {
                "ok": True,
                "mode": mode,
                "message": f"SiYuan MCP (stdio) 连接成功，发现 {len(tools)} 个工具。",
                "tools": [{"name": t.get("name"), "description": t.get("description", "")} for t in tools],
            }
    except Exception as exc:
        return {
            "ok": False,
            "mode": mode,
            "message": f"SiYuan MCP (stdio) 启动或连接失败: {exc}",
            "tools": [],
        }


def _pick_mcp_tool(tools: List[Dict[str, Any]], keyword: str, sql_stmt: str) -> Optional[Tuple[str, Dict[str, Any]]]:
    # Prefer SQL query tool when available
    for t in tools:
        name = (t.get("name") or "").lower()
        schema_props = (t.get("inputSchema") or {}).get("properties") or {}
        if "sql" in name or "query" in name:
            for param in ("sql", "stmt", "query"):
                if param in schema_props:
                    return t["name"], {param: sql_stmt}
    # Next prefer search tool
    for t in tools:
        name = (t.get("name") or "").lower()
        schema_props = (t.get("inputSchema") or {}).get("properties") or {}
        if "search" in name:
            for param in ("query", "keyword", "q", "text"):
                if param in schema_props:
                    return t["name"], {param: keyword or "TODO"}
    return None


async def fetch_siyuan_notes(
    settings: Dict[str, str],
    keyword: str = "",
    custom_sql: str = "",
    limit: int = 20,
) -> Dict[str, Any]:
    mode = settings.get("siyuan_mode", "mcp_stdio")
    safe_kw = keyword.replace("'", "''").strip()
    if custom_sql.strip():
        sql_stmt = custom_sql.strip()
    elif safe_kw:
        sql_stmt = (
            f"SELECT id, root_id, box, hpath, content, markdown, updated, type "
            f"FROM blocks WHERE (content LIKE '%{safe_kw}%' OR markdown LIKE '%{safe_kw}%') "
            f"ORDER BY updated DESC LIMIT {int(limit)}"
        )
    else:
        sql_stmt = settings.get("siyuan_sql_filter") or (
            f"SELECT id, root_id, box, hpath, content, markdown, updated, type FROM blocks "
            f"WHERE (markdown LIKE '%[ ]%' OR content LIKE '%提醒%' OR content LIKE '%TODO%' OR content LIKE '%待办%') "
            f"ORDER BY updated DESC LIMIT {int(limit)}"
        )

    if mode == "siyuan_api":
        api_url = (settings.get("siyuan_api_url") or "http://127.0.0.1:6806").rstrip("/")
        token = (settings.get("siyuan_token") or "").strip()
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Token {token}"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(f"{api_url}/api/query/sql", headers=headers, json={"stmt": sql_stmt})
                resp.raise_for_status()
                body = resp.json()
                if body.get("code", 0) != 0:
                    raise RuntimeError(body.get("msg") or "SiYuan SQL error")
                notes = _normalize_siyuan_blocks(body)
                return {"ok": True, "source": "siyuan_api", "demo_fallback": False, "notes": notes}
        except Exception as exc:
            return {
                "ok": False,
                "source": "siyuan_api",
                "demo_fallback": True,
                "error": f"未能连接本地思源内核 ({api_url})：{exc}。已加载示例笔记供体验。",
                "notes": SAMPLE_SIYUAN_NOTES,
            }

    if mode == "mcp_http":
        mcp_url = (settings.get("mcp_url") or "").strip()
        try:
            await _mcp_http_rpc(mcp_url, "initialize", {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "life-hub", "version": "1.0.0"}}, 1)
            tools_res = await _mcp_http_rpc(mcp_url, "tools/list", {}, 2)
            tools = tools_res.get("tools") or []
            picked = _pick_mcp_tool(tools, keyword, sql_stmt)
            if not picked:
                raise RuntimeError("MCP 服务未暴露可识别的 SQL 或 Search 工具。")
            tool_res = await _mcp_http_rpc(mcp_url, "tools/call", {"name": picked[0], "arguments": picked[1]}, 3)
            notes = _normalize_siyuan_blocks(tool_res)
            return {"ok": True, "source": f"mcp_http:{picked[0]}", "demo_fallback": False, "notes": notes}
        except Exception as exc:
            return {
                "ok": False,
                "source": "mcp_http",
                "demo_fallback": True,
                "error": f"未能连接思源 MCP HTTP ({mcp_url})：{exc}。已加载示例笔记供体验。",
                "notes": SAMPLE_SIYUAN_NOTES,
            }

    # Default: mcp_stdio
    cmd = (settings.get("mcp_command") or "npx").strip()
    args = _parse_json_setting(settings.get("mcp_args_json", ""), ["-y", "siyuan-mcp@latest"])
    env = _parse_json_setting(settings.get("mcp_env_json", ""), {})
    try:
        async with McpStdioClient(cmd, args, env, timeout_s=15.0) as client:
            tools = await client.list_tools()
            picked = _pick_mcp_tool(tools, keyword, sql_stmt)
            if not picked:
                raise RuntimeError("MCP 服务未暴露可识别的 SQL 或 Search 工具。")
            tool_res = await client.call_tool(picked[0], picked[1])
            notes = _normalize_siyuan_blocks(tool_res)
            return {"ok": True, "source": f"mcp_stdio:{picked[0]}", "demo_fallback": False, "notes": notes}
    except Exception as exc:
        return {
            "ok": False,
            "source": "mcp_stdio",
            "demo_fallback": True,
            "error": f"未能连接本地思源 MCP：{exc}。已加载示例笔记供体验。",
            "notes": SAMPLE_SIYUAN_NOTES,
        }
