"""XHS backend via xiaohongshu-mcp (streamable HTTP MCP transport).

Replaces the fragile MediaCrawler subprocess approach. Requires the
xiaohongshu-mcp Docker container running on localhost:18060.

Protocol: JSON-RPC 2.0 over HTTP POST to /mcp, with session management
via the Mcp-Session-Id header.
"""

import json
import re
import sys
import urllib.parse
import urllib.request

MCP_URL = "http://localhost:18060/mcp"
TIMEOUT = 45  # seconds per tool call


class XHSMCPError(Exception):
    """Error communicating with xiaohongshu-mcp."""


class XHSNotLoggedIn(XHSMCPError):
    """XHS session has no valid cookies."""


class _Session:
    """Manages a streamable HTTP MCP session."""

    def __init__(self):
        self._session_id = None
        self._req_id = 0

    def _next_id(self):
        self._req_id += 1
        return self._req_id

    def _post(self, payload, *, timeout=TIMEOUT):
        """Send a JSON-RPC request and return the parsed response."""
        data = json.dumps(payload).encode()
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id

        req = urllib.request.Request(MCP_URL, data=data, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                # Capture session ID from response headers
                sid = resp.headers.get("Mcp-Session-Id")
                if sid:
                    self._session_id = sid
                body = resp.read().decode()
                if not body:
                    return None
                return json.loads(body)
        except (TimeoutError, OSError) as e:
            raise XHSMCPError(
                f"Request to xiaohongshu-mcp timed out after {timeout}s. "
                f"The server may be waiting for login. ({e})"
            ) from e
        except urllib.error.URLError as e:
            raise XHSMCPError(
                f"Cannot reach xiaohongshu-mcp at {MCP_URL}. "
                f"Is the Docker container running? ({e})"
            ) from e

    def _notify(self, method, params=None):
        """Send a JSON-RPC notification (no id, no response expected)."""
        payload = {"jsonrpc": "2.0", "method": method}
        if params:
            payload["params"] = params
        data = json.dumps(payload).encode()
        headers = {"Content-Type": "application/json"}
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        req = urllib.request.Request(MCP_URL, data=data, headers=headers)
        try:
            urllib.request.urlopen(req, timeout=5)
        except Exception:
            pass  # Notifications are fire-and-forget

    def initialize(self):
        """Perform MCP initialize handshake."""
        resp = self._post({
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "qianli", "version": "0.3.0"},
            },
        })
        if not resp or "result" not in resp:
            raise XHSMCPError(f"MCP initialize failed: {resp}")
        self._notify("notifications/initialized")
        return resp["result"]

    def call_tool(self, name, arguments=None, *, timeout=TIMEOUT):
        """Call an MCP tool and return its result content."""
        resp = self._post(
            {
                "jsonrpc": "2.0",
                "id": self._next_id(),
                "method": "tools/call",
                "params": {
                    "name": name,
                    "arguments": arguments or {},
                },
            },
            timeout=timeout,
        )
        if not resp:
            raise XHSMCPError(f"No response from tool {name}")
        if "error" in resp:
            raise XHSMCPError(f"MCP error: {resp['error']}")
        result = resp.get("result", {})
        if result.get("isError"):
            text = _extract_text(result)
            if "未登录" in text or "login" in text.lower():
                raise XHSNotLoggedIn(text)
            raise XHSMCPError(text)
        return result


def _extract_text(result):
    """Extract text content from MCP tool result."""
    contents = result.get("content", [])
    parts = []
    for c in contents:
        if c.get("type") == "text":
            parts.append(c["text"])
    return "\n".join(parts)


def _get_session():
    """Create and initialize an MCP session."""
    s = _Session()
    s.initialize()
    return s


def _parse_feed_items(text):
    """Parse feed items from the MCP response text.

    The response is typically formatted as markdown or structured text.
    We attempt JSON first, then fall back to text parsing.
    """
    # Try to find a JSON array in the response
    json_match = re.search(r'\[[\s\S]*\]', text)
    if json_match:
        try:
            return json.loads(json_match.group())
        except json.JSONDecodeError:
            pass

    # Parse structured text response — the MCP server returns formatted text
    # with fields like feed_id, title, xsec_token, etc.
    items = []
    current = {}
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            if current:
                items.append(current)
                current = {}
            continue

        # Match "key: value" or "**key:** value" patterns
        m = re.match(r'\*?\*?(\w[\w_]*)\*?\*?\s*[:：]\s*(.*)', line)
        if m:
            key = m.group(1).lower().strip("*")
            val = m.group(2).strip()
            current[key] = val
        # Match "- key: value" patterns
        m2 = re.match(r'[-•]\s*\*?\*?(\w[\w_]*)\*?\*?\s*[:：]\s*(.*)', line)
        if m2:
            key = m2.group(1).lower().strip("*")
            val = m2.group(2).strip()
            current[key] = val

    if current:
        items.append(current)
    return items


def _normalize(items, limit):
    """Normalize parsed items to qianli result format."""
    results = []
    for item in items[:limit]:
        feed_id = (
            item.get("feed_id")
            or item.get("id")
            or item.get("note_id")
            or ""
        )
        url = item.get("url", "")
        if not url and feed_id:
            url = f"https://www.xiaohongshu.com/explore/{feed_id}"

        title = (
            item.get("title")
            or item.get("display_title")
            or item.get("name")
            or ""
        )
        snippet = (
            item.get("desc")
            or item.get("description")
            or item.get("content")
            or item.get("snippet")
            or ""
        )
        author = (
            item.get("nickname")
            or item.get("author")
            or item.get("user_nickname")
            or ""
        )
        if author and not author.startswith("@"):
            author = f"@{author}"

        likes = str(
            item.get("liked_count")
            or item.get("likes")
            or item.get("like_count")
            or ""
        )

        results.append({
            "source": "xhs",
            "title": title,
            "url": url,
            "snippet": snippet[:120] if snippet else "",
            "author": author,
            "date": item.get("time", item.get("date", item.get("created_at", ""))),
            "likes": likes,
            # Preserve for get_feed_detail calls
            "feed_id": feed_id,
            "xsec_token": item.get("xsec_token", item.get("xsectoken", "")),
        })
    return results


def search_xhs(query, limit=5):
    """Search XHS via xiaohongshu-mcp.

    Args:
        query: Search query string.
        limit: Max results to return.

    Returns:
        List of normalized result dicts.
    """
    try:
        session = _get_session()
    except XHSMCPError as e:
        print(f"[xhs] {e}", file=sys.stderr)
        return []

    try:
        result = session.call_tool("search_feeds", {"keyword": query})
    except XHSNotLoggedIn:
        print(
            "[xhs] Not logged in. Export cookies via Cookie-Editor and "
            "place them in ~/code/xiaohongshu-mcp/data/cookies.json, "
            "then restart the container.",
            file=sys.stderr,
        )
        return []
    except XHSMCPError as e:
        print(f"[xhs] Search failed: {e}", file=sys.stderr)
        return []

    text = _extract_text(result)
    if not text:
        return []

    items = _parse_feed_items(text)
    return _normalize(items, limit)


def get_feed_detail(feed_id, xsec_token):
    """Get full detail for a single XHS post.

    Args:
        feed_id: The XHS note ID.
        xsec_token: Security token from search results.

    Returns:
        Dict with post content, or None on failure.
    """
    try:
        session = _get_session()
        result = session.call_tool("get_feed_detail", {
            "feed_id": feed_id,
            "xsec_token": xsec_token,
        })
        return _extract_text(result)
    except XHSMCPError as e:
        print(f"[xhs] Detail fetch failed: {e}", file=sys.stderr)
        return None


def check_status():
    """Check if xiaohongshu-mcp is running and logged in.

    Returns:
        Tuple of (is_running: bool, is_logged_in: bool, message: str).
    """
    try:
        session = _get_session()
    except XHSMCPError as e:
        return False, False, str(e)

    try:
        result = session.call_tool("check_login_status", timeout=15)
        text = _extract_text(result)
        logged_in = "已登录" in text or "logged in" in text.lower()
        return True, logged_in, text
    except XHSMCPError as e:
        return True, False, str(e)
