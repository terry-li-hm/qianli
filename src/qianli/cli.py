"""qianli: Search Chinese content platforms.

Sources: WeChat (Exauro), 36kr (agent-browser), XHS (xiaohongshu-mcp), Zhihu (MediaCrawler).
"""

import argparse
import json
import sys
import subprocess
import re
from urllib.parse import quote

from qianli.xhs_mcp import search_xhs, check_status as xhs_check_status


# --- JS extractors ---

JS_36KR = """(() => { const items = document.querySelectorAll('.kr-flow-article-item'); const results = []; for (const item of items) { const linkEl = item.querySelector('a[href*="/p/"]'); const titleEl = item.querySelector('.article-item-title'); const descEl = item.querySelector('.article-item-description'); const timeEl = item.querySelector('.kr-flow-bar-time'); const href = (linkEl && linkEl.getAttribute('href')) || ''; const title = (titleEl && titleEl.textContent && titleEl.textContent.trim()) || ''; const desc = (descEl && descEl.textContent && descEl.textContent.trim()) || ''; const date = (timeEl && timeEl.textContent && timeEl.textContent.trim()) || ''; if (title && href) { results.push({ source: '36kr', title, url: href.startsWith('/') ? 'https://36kr.com' + href : href, snippet: desc.substring(0, 120), author: '36氪', date }); } } return JSON.stringify(results); })()"""


# --- Search functions ---


def search_wechat(query, limit=5):
    """Search WeChat articles via exauro CLI."""
    cmd = ["exauro", "search", f"{query} site:mp.weixin.qq.com", "--search-type", "auto"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
        lines = proc.stdout.splitlines()

        results = []
        current_item = None

        for line in lines:
            line = line.strip()
            if not line:
                continue

            # Match "N. <title>"
            match = re.match(r"^\d+\.\s+(.+)$", line)
            if match:
                if current_item and "title" in current_item:
                    results.append(current_item)
                current_item = {
                    "source": "wechat",
                    "title": match.group(1),
                    "author": "",
                    "date": ""
                }
            elif current_item:
                if "url" not in current_item:
                    if line.startswith("http"):
                        current_item["url"] = line
                elif "snippet" not in current_item:
                    current_item["snippet"] = line

        if current_item and "title" in current_item:
            results.append(current_item)

        return results[:limit]
    except subprocess.CalledProcessError as e:
        print(f"[wechat] Error: exauro failed: {e.stderr}", file=sys.stderr)
        return []
    except Exception as e:
        print(f"[wechat] Error: {e}", file=sys.stderr)
        return []


def search_36kr(query, limit=5):
    """Search 36kr articles via agent-browser CLI."""
    url = f"https://36kr.com/search/articles/{quote(query)}"
    # Escaping JS for shell
    js_escaped = JS_36KR.replace("'", "'''")
    cmd = f"agent-browser open '{url}' && (agent-browser wait '.kr-flow-article-item' || echo 'timeout') && agent-browser eval '{js_escaped}' && agent-browser close"
    try:
        proc = subprocess.run(cmd, shell=True, capture_output=True, text=True, check=True)
        result_json = proc.stdout.strip()

        if not result_json or "timeout" in result_json.lower():
            return []

        match = re.search(r"(\[.*\])", result_json, re.DOTALL)
        if match:
            items = json.loads(match.group(1))
            return items[:limit]
        return []
    except subprocess.CalledProcessError as e:
        print(f"[36kr] Error: agent-browser failed: {e.stderr}", file=sys.stderr)
        subprocess.run("agent-browser close", shell=True, capture_output=True)
        return []
    except Exception as e:
        print(f"[36kr] Error: {e}", file=sys.stderr)
        subprocess.run("agent-browser close", shell=True, capture_output=True)
        return []


def search_zhihu(query, limit=5):
    """Search Zhihu content via MediaCrawler."""
    try:
        from qianli.mc import run_mc_search
        return run_mc_search("zhihu", query, limit)
    except Exception as e:
        print(f"[zhihu] Error: {e}", file=sys.stderr)
        return []


def read_url(url):
    """Open a URL and return the page content as text."""
    js = "document.body ? document.body.innerText : ''"
    cmd = f'agent-browser open "{url}" && agent-browser eval "{js}" && agent-browser close'
    try:
        proc = subprocess.run(cmd, shell=True, capture_output=True, text=True, check=True)
        text = proc.stdout.strip()
        if text:
            print(text)
        else:
            print("Error: failed to extract page content", file=sys.stderr)
    except subprocess.CalledProcessError as e:
        print(f"Error: agent-browser failed: {e.stderr}", file=sys.stderr)
        subprocess.run("agent-browser close", shell=True, capture_output=True)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        subprocess.run("agent-browser close", shell=True, capture_output=True)


def doctor():
    """Check health of all backends."""
    print("qianli doctor\n")

    # XHS (xiaohongshu-mcp)
    running, logged_in, msg = xhs_check_status()
    if running and logged_in:
        print("[xhs] OK — xiaohongshu-mcp running, logged in")
    elif running:
        print(f"[xhs] WARN — xiaohongshu-mcp running but not logged in")
        print(f"       {msg}")
    else:
        print(f"[xhs] FAIL — {msg}")

    # Zhihu (MediaCrawler)
    try:
        from qianli.mc import MC_DIR, MC_PYTHON
        if MC_DIR.exists() and MC_PYTHON.exists():
            print("[zhihu] OK — MediaCrawler found")
        elif MC_DIR.exists():
            print("[zhihu] WARN — MediaCrawler found but venv missing")
        else:
            print("[zhihu] SKIP — MediaCrawler not installed")
    except Exception as e:
        print(f"[zhihu] FAIL — {e}")

    # Exauro (WeChat)
    try:
        proc = subprocess.run(["exauro", "--version"], capture_output=True, text=True, timeout=5)
        if proc.returncode == 0:
            print(f"[wechat] OK — exauro {proc.stdout.strip()}")
        else:
            print("[wechat] WARN — exauro found but returned error")
    except FileNotFoundError:
        print("[wechat] FAIL — exauro not found")
    except Exception as e:
        print(f"[wechat] FAIL — {e}")

    # agent-browser (36kr)
    try:
        proc = subprocess.run(["agent-browser", "--version"], capture_output=True, text=True, timeout=5)
        if proc.returncode == 0:
            print(f"[36kr] OK — agent-browser {proc.stdout.strip()}")
        else:
            print("[36kr] WARN — agent-browser found but returned error")
    except FileNotFoundError:
        print("[36kr] FAIL — agent-browser not found")
    except Exception as e:
        print(f"[36kr] FAIL — {e}")


# --- Output ---


def format_text(results):
    """Format results as compact text."""
    for r in results:
        tag = r["source"]
        title = r["title"]
        meta_parts = []
        if r.get("author"):
            meta_parts.append(str(r["author"]))
        if r.get("date"):
            meta_parts.append(str(r["date"]))
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

ALL_SOURCES = {
    "wechat": search_wechat,
    "36kr": search_36kr,
    "xhs": search_xhs,
    "zhihu": search_zhihu,
}

# Sources included in `qianli all`
ALL_SEARCH_SOURCES = ["wechat", "36kr", "xhs"]


def main():
    parser = argparse.ArgumentParser(
        description="Search Chinese content platforms"
    )
    sub = parser.add_subparsers(dest="command")

    for name in ALL_SOURCES:
        p = sub.add_parser(name, help=f"Search {name}")
        p.add_argument("query", help="Search query")
        p.add_argument("--limit", type=int, default=5)
        p.add_argument("--json", action="store_true", dest="json_out")

    p_all = sub.add_parser("all", help="Search wechat + 36kr + xhs")
    p_all.add_argument("query", help="Search query")
    p_all.add_argument("--limit", type=int, default=3)
    p_all.add_argument("--json", action="store_true", dest="json_out")

    p_read = sub.add_parser("read", help="Read full content from a URL")
    p_read.add_argument("url", help="URL to read")

    sub.add_parser("doctor", help="Check health of all backends")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command == "doctor":
        doctor()
        return

    if args.command == "read":
        read_url(args.url)
        return

    if args.command == "all":
        all_results = []
        for name in ALL_SEARCH_SOURCES:
            fn = ALL_SOURCES[name]
            try:
                results = fn(args.query, args.limit)
                all_results.extend(results)
            except Exception as e:
                print(f"[{name}] Error: {e}", file=sys.stderr)
        if args.json_out:
            format_json(all_results)
        else:
            format_text(all_results)
        return

    if args.command in ALL_SOURCES:
        results = ALL_SOURCES[args.command](args.query, args.limit)
        if args.json_out:
            format_json(results)
        else:
            format_text(results)
        return


if __name__ == "__main__":
    main()
