"""Post a per-run summary to the System repo so it owns the audit trail."""

from __future__ import annotations

import argparse
import json
import os
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

SYSTEM_REPO = os.environ.get("SYSTEM_REPO", "dh914/System")
AGENT_REPO = os.environ.get("AGENT_REPO", "dh914/Agent")
TOKEN = os.environ.get("GITHUB_TOKEN", "")
LOG_DIR = Path(__file__).resolve().parent.parent / "logs"


def _latest_log() -> dict | None:
    files = sorted(LOG_DIR.glob("*.json"))
    if not files:
        return None
    return json.loads(files[-1].read_text(encoding="utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", default="unknown")
    ap.add_argument("--run-id", default="")
    args = ap.parse_args()

    if not TOKEN:
        print("[system] no token; skipping report.")
        return 0

    log = _latest_log() or {"entries": []}
    when = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    title = f"[routine] email cycle @ {when}"
    body_lines = [
        f"- status: `{args.status}`",
        f"- agent run: https://github.com/{AGENT_REPO}/actions/runs/{args.run_id}",
        f"- entries: {len(log.get('entries', []))}",
        "",
        "```json",
        json.dumps(log, ensure_ascii=False, indent=2)[:5000],
        "```",
    ]
    payload = json.dumps(
        {"title": title, "body": "\n".join(body_lines), "labels": ["email-routine", "audit"]}
    ).encode()
    req = urllib.request.Request(
        f"https://api.github.com/repos/{SYSTEM_REPO}/issues",
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            print(f"[system] recorded: {json.loads(resp.read()).get('html_url')}")
    except Exception as exc:
        print(f"[system] failed to record: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
