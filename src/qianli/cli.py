"""qianli: Search Chinese content platforms via CDP Chrome.

Sources: WeChat 公众号 (Sogou), 36kr, XHS (小红书).
Connects directly to Chrome DevTools Protocol on port 9222.
"""

import argparse
import asyncio
import json
import re
import sys
import time
import urllib.request
from urllib.parse import quote

CDP_PORT = 9222
CDP_BASE = f"http://localhost:{CDP_PORT}"
WS_MAX_SIZE = 10_000_000


# --- CDP helpers ---


def _get_browser_ws():
    """Get browser-level websocket URL."""
    data = json.loads(urllib.request.urlopen(f"{CDP_BASE}/json/version").read())
    return data["webSocketDebuggerUrl"]


def _get_tabs():
    """Get list of open tabs."""
    return json.loads(urllib.request.urlopen(f"{CDP_BASE}/json").read())


def _cdp_check():
    """Check if CDP Chrome is reachable."""
    try:
        urllib.request.urlopen(f"{CDP_BASE}/json/version", timeout=2)
        return True
    except Exception:
        return False


async def _create_tab(url):
    """Create a new tab and return its target ID."""
    import websockets

    browser_ws = _get_browser_ws()
    async with websockets.connect(browser_ws, max_size=WS_MAX_SIZE) as ws:
        await ws.send(
            json.dumps(
                {
                    "id": 1,
                    "method": "Target.createTarget",
                    "params": {"url": url},
                }
            )
        )
        while True:
            msg = await asyncio.wait_for(ws.recv(), timeout=10)
            data = json.loads(msg)
            if data.get("id") == 1:
                return data["result"]["targetId"]


async def _close_tab(target_id):
    """Close a tab by target ID."""
    import websockets

    browser_ws = _get_browser_ws()
    async with websockets.connect(browser_ws, max_size=WS_MAX_SIZE) as ws:
        await ws.send(
            json.dumps(
                {
                    "id": 1,
                    "method": "Target.closeTarget",
                    "params": {"targetId": target_id},
                }
            )
        )
        try:
            await asyncio.wait_for(ws.recv(), timeout=3)
        except asyncio.TimeoutError:
            pass


async def _evaluate(ws_url, expression, timeout=10):
    """Evaluate JS expression in a tab and return the value."""
    import websockets

    async with websockets.connect(ws_url, max_size=WS_MAX_SIZE) as ws:
        await ws.send(
            json.dumps(
                {
                    "id": 1,
                    "method": "Runtime.evaluate",
                    "params": {"expression": expression, "returnByValue": True},
                }
            )
        )
        while True:
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=timeout)
                data = json.loads(msg)
                if data.get("id") == 1:
                    result = data.get("result", {}).get("result", {})
                    if "value" in result:
                        return result["value"]
                    return None
            except asyncio.TimeoutError:
                return None


def _open_and_extract(url, js_extractor, wait_secs=4, max_wait=15, ready_check=None):
    """Open URL in new tab, wait for content, run JS extractor, close tab.

    Args:
        url: URL to navigate to
        js_extractor: JS expression that returns JSON string of results
        wait_secs: Initial wait before first check
        max_wait: Maximum total wait time
        ready_check: JS expression returning truthy when page is ready (optional)
    """
    target_id = asyncio.run(_create_tab(url))

    try:
        elapsed = 0
        time.sleep(wait_secs)
        elapsed += wait_secs

        while elapsed < max_wait:
            tabs = _get_tabs()
            tab = next((t for t in tabs if t.get("id") == target_id), None)
            if not tab:
                return None

            ws_url = tab["webSocketDebuggerUrl"]

            # Check if page is ready
            if ready_check:
                ready = asyncio.run(_evaluate(ws_url, ready_check, timeout=5))
                if not ready:
                    time.sleep(2)
                    elapsed += 2
                    continue

            # Try extracting
            result = asyncio.run(_evaluate(ws_url, js_extractor, timeout=10))
            if result:
                return result

            time.sleep(2)
            elapsed += 2

        return None
    finally:
        asyncio.run(_close_tab(target_id))


# --- JS extractors ---

JS_WECHAT = """
(() => {
    const items = document.querySelectorAll('#main .txt-box');
    const results = [];
    for (const item of items) {
        const li = item.closest('li');
        const titleEl = item.querySelector('h3 a');
        const snippetEl = item.querySelector('p.txt-info, p');
        const accountEl = li?.querySelector('span.all-time-y2');
        const dateEl = li?.querySelector('span.s2');

        const title = titleEl?.textContent?.trim() || '';
        const href = titleEl?.getAttribute('href') || '';
        const snippet = snippetEl?.textContent?.trim() || '';
        const account = accountEl?.textContent?.trim() || '';
        const dateText = dateEl?.textContent?.trim() || '';
        const dateMatch = dateText.match(/(\\d{4}-\\d{1,2}-\\d{1,2})/);
        const date = dateMatch ? dateMatch[1] : '';

        if (title && href) {
            results.push({
                source: 'wechat',
                title,
                url: href.startsWith('/') ? 'https://weixin.sogou.com' + href : href,
                snippet: snippet.substring(0, 120),
                author: account,
                date
            });
        }
    }
    return JSON.stringify(results);
})()
"""

JS_36KR = """
(() => {
    const items = document.querySelectorAll('.kr-flow-article-item');
    const results = [];
    for (const item of items) {
        const linkEl = item.querySelector('a[href*="/p/"]');
        const titleEl = item.querySelector('.article-item-title');
        const descEl = item.querySelector('.article-item-description');
        const timeEl = item.querySelector('.kr-flow-bar-time');

        const href = linkEl?.getAttribute('href') || '';
        const title = titleEl?.textContent?.trim() || '';
        const desc = descEl?.textContent?.trim() || '';
        const date = timeEl?.textContent?.trim() || '';

        if (title && href) {
            results.push({
                source: '36kr',
                title,
                url: href.startsWith('/') ? 'https://36kr.com' + href : href,
                snippet: desc.substring(0, 120),
                author: '36氪',
                date
            });
        }
    }
    return JSON.stringify(results);
})()
"""

JS_XHS = """
(() => {
    const items = document.querySelectorAll('section.note-item');
    const results = [];
    for (const item of items) {
        const titleEl = item.querySelector('.footer .title span');
        const authorEl = item.querySelector('.author .name');
        const linkEl = item.querySelector('a.cover');
        const likeEl = item.querySelector('.like-wrapper .count');
        const footerText = item.querySelector('.footer')?.innerText || '';

        const title = titleEl?.textContent?.trim() || '';
        const author = authorEl?.textContent?.trim() || '';
        const href = linkEl?.getAttribute('href') || '';
        const likes = likeEl?.textContent?.trim() || '';

        // Extract date from footer text (title\\nauthor\\ndate\\nlikes)
        const lines = footerText.split('\\n').map(l => l.trim()).filter(Boolean);
        let date = '';
        for (const line of lines) {
            if (/^\\d{4}-\\d{2}-\\d{2}$/.test(line) || /^\\d{2}-\\d{2}$/.test(line) || /小时前|天前|昨天/.test(line)) {
                date = line;
                break;
            }
        }

        // Extract note ID from href for canonical URL
        const idMatch = href.match(/\\/(explore|search_result)\\/([a-f0-9]+)/);
        const noteId = idMatch ? idMatch[2] : '';
        const canonicalUrl = noteId ? 'https://www.xiaohongshu.com/explore/' + noteId : '';

        if (title && canonicalUrl) {
            results.push({
                source: 'xhs',
                title,
                url: canonicalUrl,
                author: '@' + author,
                date,
                likes
            });
        }
    }
    return JSON.stringify(results);
})()
"""


# --- Search functions ---


def search_wechat(query, limit=5):
    """Search WeChat articles via Sogou."""
    url = f"https://weixin.sogou.com/weixin?type=2&query={quote(query)}"
    result = _open_and_extract(
        url,
        JS_WECHAT,
        wait_secs=4,
        max_wait=12,
        ready_check="document.querySelectorAll('#main .txt-box').length > 0",
    )
    if not result:
        print("[wechat] Error: failed to load search results", file=sys.stderr)
        return []
    items = json.loads(result)
    return items[:limit]


def search_36kr(query, limit=5):
    """Search 36kr articles."""
    url = f"https://36kr.com/search/articles/{quote(query)}"
    result = _open_and_extract(
        url,
        JS_36KR,
        wait_secs=6,
        max_wait=18,
        ready_check="document.querySelectorAll('.kr-flow-article-item').length > 0",
    )
    if not result:
        print("[36kr] Error: page did not render (SPA timeout)", file=sys.stderr)
        return []
    items = json.loads(result)
    return items[:limit]


def search_xhs(query, limit=5):
    """Search XHS posts (requires CDP login)."""
    url = f"https://www.xiaohongshu.com/search_result?keyword={quote(query)}&type=51"
    result = _open_and_extract(
        url,
        JS_XHS,
        wait_secs=4,
        max_wait=15,
        ready_check="document.querySelectorAll('section.note-item').length > 0",
    )
    if not result:
        # Check if it's a login wall
        print(
            "[xhs] Error: no results (not logged in, or page did not load)",
            file=sys.stderr,
        )
        return []
    items = json.loads(result)
    return items[:limit]


def read_url(url):
    """Open a URL and return the page content as text."""
    target_id = asyncio.run(_create_tab(url))
    try:
        time.sleep(5)
        tabs = _get_tabs()
        tab = next((t for t in tabs if t.get("id") == target_id), None)
        if not tab:
            print("Error: tab not found", file=sys.stderr)
            return
        ws_url = tab["webSocketDebuggerUrl"]
        text = asyncio.run(_evaluate(ws_url, "document.body?.innerText || ''", timeout=10))
        if text:
            print(text)
        else:
            print("Error: failed to extract page content", file=sys.stderr)
    finally:
        asyncio.run(_close_tab(target_id))


# --- Output ---


def format_text(results):
    """Format results as compact text."""
    for r in results:
        tag = r["source"]
        title = r["title"]
        meta_parts = []
        if r.get("author"):
            meta_parts.append(r["author"])
        if r.get("date"):
            meta_parts.append(r["date"])
        if r.get("likes"):
            meta_parts.append(f"❤ {r['likes']}")
        meta = " · ".join(meta_parts)
        url = r.get("url", "")
        snippet = r.get("snippet", "")

        print(f"[{tag}] {title}")
        if meta:
            print(f"{'':>{len(tag)+3}}{meta}")
        if url:
            print(f"{'':>{len(tag)+3}}{url}")
        if snippet:
            print(f"{'':>{len(tag)+3}}{snippet}")
        print()


def format_json(results):
    """Format results as JSON."""
    print(json.dumps(results, ensure_ascii=False, indent=2))


# --- CLI ---

SOURCES = {
    "wechat": search_wechat,
    "36kr": search_36kr,
    "xhs": search_xhs,
}


def main():
    parser = argparse.ArgumentParser(
        description="Search Chinese content platforms via CDP Chrome"
    )
    sub = parser.add_subparsers(dest="command")

    for name in SOURCES:
        p = sub.add_parser(name, help=f"Search {name}")
        p.add_argument("query", help="Search query")
        p.add_argument("--limit", type=int, default=5)
        p.add_argument("--json", action="store_true", dest="json_out")

    p_all = sub.add_parser("all", help="Search all available sources")
    p_all.add_argument("query", help="Search query")
    p_all.add_argument("--limit", type=int, default=3)
    p_all.add_argument("--json", action="store_true", dest="json_out")

    p_read = sub.add_parser("read", help="Read full content from a URL")
    p_read.add_argument("url", help="URL to read")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    if not _cdp_check():
        print(
            'Error: CDP Chrome not running. Start it: open "/Applications/Chrome CDP.app"',
            file=sys.stderr,
        )
        sys.exit(1)

    if args.command == "read":
        read_url(args.url)
        return

    if args.command == "all":
        all_results = []
        for name in SOURCES:
            try:
                results = SOURCES[name](args.query, args.limit)
                all_results.extend(results)
            except Exception as e:
                print(f"[{name}] Error: {e}", file=sys.stderr)
        if args.json_out:
            format_json(all_results)
        else:
            format_text(all_results)
        return

    if args.command in SOURCES:
        results = SOURCES[args.command](args.query, args.limit)
        if args.json_out:
            format_json(results)
        else:
            format_text(results)
        return


if __name__ == "__main__":
    main()
