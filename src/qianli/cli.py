"""qianli: Search Chinese content platforms.

Sources: WeChat 公众号 via Exa (exauro), 36kr via agent-browser. XHS, Zhihu via MediaCrawler.
No CDP Chrome required.
"""

import argparse
import json
import re
import subprocess
import sys

from qianli.mc import run_mc_search




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
            m = re.match(r"^\d+\.\s+(.+)$", line)
            if m:
                if current_item and "url" in current_item:
                    results.append(current_item)
                current_item = {
                    "source": "wechat",
                    "title": m.group(1),
                    "url": "",
                    "snippet": "",
                    "author": "",
                    "date": "",
                }
            elif current_item:
                if not current_item["url"] and line.startswith("http"):
                    current_item["url"] = line
                elif not current_item["snippet"] and current_item["url"]:
                    current_item["snippet"] = line

        if current_item and current_item["url"]:
            results.append(current_item)

        return results[:limit]
    except subprocess.CalledProcessError as e:
        print(f"[wechat] Error: exauro failed: {e.stderr}", file=sys.stderr)
        return []
    except Exception as e:
        print(f"[wechat] Error: {e}", file=sys.stderr)
        return []


def search_36kr(query, limit=5):
    """Search 36kr articles via exauro CLI."""
    cmd = ["exauro", "search", f"{query} site:36kr.com", "--search-type", "auto"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
        lines = proc.stdout.splitlines()

        results = []
        current_item = None

        for line in lines:
            line = line.strip()
            if not line:
                continue

            m = re.match(r"^\d+\.\s+(.+)$", line)
            if m:
                if current_item and current_item["url"]:
                    results.append(current_item)
                current_item = {
                    "source": "36kr",
                    "title": m.group(1),
                    "url": "",
                    "snippet": "",
                    "author": "36氪",
                    "date": "",
                }
            elif current_item:
                if not current_item["url"] and line.startswith("http"):
                    current_item["url"] = line
                elif not current_item["snippet"] and current_item["url"]:
                    current_item["snippet"] = line

        if current_item and current_item["url"]:
            results.append(current_item)

        return results[:limit]
    except subprocess.CalledProcessError as e:
        print(f"[36kr] Error: exauro failed: {e.stderr}", file=sys.stderr)
        return []
    except Exception as e:
        print(f"[36kr] Error: {e}", file=sys.stderr)
        return []


def search_xhs(query, limit=5):
    """Search XHS posts via MediaCrawler."""
    return run_mc_search("xhs", query, limit)


def search_zhihu(query, limit=5):
    """Search Zhihu content via MediaCrawler."""
    return run_mc_search("zhihu", query, limit)


def read_url(url):
    """Open a URL and return the page content as text."""
    try:
        subprocess.run(["agent-browser", "open", url], check=True, capture_output=True)
        proc = subprocess.run(
            ["agent-browser", "eval", "document.body?.innerText || ''"],
            capture_output=True, text=True, check=True,
        )
        subprocess.run(["agent-browser", "close"], capture_output=True)
        text = proc.stdout.strip()
        if text:
            print(text)
        else:
            print("Error: failed to extract page content", file=sys.stderr)
    except subprocess.CalledProcessError as e:
        print(f"Error: agent-browser failed: {e.stderr}", file=sys.stderr)
        subprocess.run(["agent-browser", "close"], capture_output=True)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        subprocess.run(["agent-browser", "close"], capture_output=True)


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

MC_SOURCES = {
    "xhs": search_xhs,
    "zhihu": search_zhihu,
}

ALL_SOURCES = {
    "wechat": search_wechat,
    "36kr": search_36kr,
    **MC_SOURCES,
}


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

    p_all = sub.add_parser("all", help="Search wechat + 36kr")
    p_all.add_argument("query", help="Search query")
    p_all.add_argument("--limit", type=int, default=3)
    p_all.add_argument("--json", action="store_true", dest="json_out")

    p_read = sub.add_parser("read", help="Read full content from a URL")
    p_read.add_argument("url", help="URL to read")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command == "read":
        read_url(args.url)
        return

    if args.command == "all":
        all_results = []
        for name in ["wechat", "36kr"]:
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
