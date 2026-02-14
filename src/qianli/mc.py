"""MediaCrawler backend for XHS and Zhihu searches.

Runs MediaCrawler as a subprocess, reads JSON output, normalizes results.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

MC_DIR = Path.home() / "code" / "MediaCrawler"
MC_PYTHON = MC_DIR / ".venv" / "bin" / "python"
MC_CONFIG = MC_DIR / "config" / "base_config.py"


def _check_mc():
    """Check MediaCrawler is installed."""
    if not MC_DIR.exists():
        print(
            "Error: MediaCrawler not found at ~/code/MediaCrawler\n"
            "Install: cd ~/code && git clone https://github.com/NanmiCoder/MediaCrawler.git\n"
            "Then: cd MediaCrawler && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt",
            file=sys.stderr,
        )
        sys.exit(1)
    if not MC_PYTHON.exists():
        print(
            "Error: MediaCrawler venv not found.\n"
            "Run: cd ~/code/MediaCrawler && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt",
            file=sys.stderr,
        )
        sys.exit(1)


def _patch_max_notes(limit):
    """Temporarily patch CRAWLER_MAX_NOTES_COUNT in config."""
    text = MC_CONFIG.read_text()
    original = text
    text = re.sub(
        r"^CRAWLER_MAX_NOTES_COUNT\s*=\s*\d+",
        f"CRAWLER_MAX_NOTES_COUNT = {limit}",
        text,
        flags=re.MULTILINE,
    )
    MC_CONFIG.write_text(text)
    return original


def _find_contents_json(output_dir, platform):
    """Find the contents JSON file in MediaCrawler output."""
    json_dir = Path(output_dir) / platform / "json"
    if not json_dir.exists():
        return None
    for f in json_dir.iterdir():
        if f.name.startswith("search_contents_") and f.suffix == ".json":
            return f
    return None


def _ts_to_date(ts):
    """Convert unix timestamp (ms or s) to YYYY-MM-DD."""
    if not ts:
        return ""
    try:
        ts = int(ts)
        if ts > 1e12:  # milliseconds
            ts = ts // 1000
        from datetime import datetime
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
    except (ValueError, OSError):
        return str(ts)


def _normalize_xhs(items):
    """Normalize XHS results to qianli format."""
    results = []
    for item in items:
        note_url = item.get("note_url", "")
        if not note_url:
            note_id = item.get("note_id", "")
            if note_id:
                note_url = f"https://www.xiaohongshu.com/explore/{note_id}"
        results.append(
            {
                "source": "xhs",
                "title": item.get("title", ""),
                "url": note_url,
                "snippet": (item.get("desc") or "")[:120],
                "author": f"@{item.get('nickname', '')}",
                "date": _ts_to_date(item.get("time")),
                "likes": str(item.get("liked_count", "")),
            }
        )
    return results


def _normalize_zhihu(items):
    """Normalize Zhihu results to qianli format."""
    results = []
    for item in items:
        results.append(
            {
                "source": "zhihu",
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "snippet": (item.get("desc") or "")[:120],
                "author": item.get("nickname", ""),
                "date": _ts_to_date(item.get("time")),
            }
        )
    return results


NORMALIZERS = {
    "xhs": _normalize_xhs,
    "zhihu": _normalize_zhihu,
}


def run_mc_search(platform, query, limit=5):
    """Run MediaCrawler search and return normalized results.

    Args:
        platform: "xhs" or "zhihu"
        query: Search query string
        limit: Max results to return

    Returns:
        List of normalized result dicts
    """
    _check_mc()

    output_dir = tempfile.mkdtemp(prefix="qianli-mc-")
    original_config = None

    try:
        # Patch max notes count
        original_config = _patch_max_notes(limit)

        cmd = [
            str(MC_PYTHON),
            "main.py",
            "--platform", platform,
            "--type", "search",
            "--keywords", query,
            "--headless", "true",
            "--get_comment", "false",
            "--save_data_option", "json",
            "--save_data_path", output_dir,
        ]

        print(f"[{platform}] Searching via MediaCrawler...", file=sys.stderr)

        result = subprocess.run(
            cmd,
            cwd=str(MC_DIR),
            capture_output=True,
            timeout=120,
        )

        if result.returncode != 0:
            stderr = result.stderr.decode("utf-8", errors="replace").strip()
            # Filter out common noise
            if stderr:
                for line in stderr.split("\n"):
                    if "error" in line.lower() or "exception" in line.lower():
                        print(f"[{platform}] {line}", file=sys.stderr)
            print(f"[{platform}] MediaCrawler exited with code {result.returncode}", file=sys.stderr)
            return []

        # Find and read the output JSON
        json_file = _find_contents_json(output_dir, platform)
        if not json_file:
            print(f"[{platform}] No results found (JSON output missing)", file=sys.stderr)
            return []

        items = json.loads(json_file.read_text(encoding="utf-8"))
        if not isinstance(items, list):
            items = [items]

        normalizer = NORMALIZERS.get(platform)
        if normalizer:
            return normalizer(items[:limit])
        return items[:limit]

    except subprocess.TimeoutExpired:
        print(f"[{platform}] MediaCrawler timed out (120s)", file=sys.stderr)
        return []
    except Exception as e:
        print(f"[{platform}] Error: {e}", file=sys.stderr)
        return []
    finally:
        # Restore config
        if original_config is not None:
            MC_CONFIG.write_text(original_config)
        # Clean up temp dir
        shutil.rmtree(output_dir, ignore_errors=True)
