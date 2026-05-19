"""Relay Data-routine results back to the original email sender.

Walks the pending dispatches recorded by ``email_routine.py``, looks at the
``data-request`` issue on the Agent repo for a result comment posted by the
Data routine, and emails the sender a summary plus a JSON attachment of the
processed result. Marks each dispatch as relayed in ``logs/_pending.json``.

The Data routine signals completion by closing the issue and embedding a
fenced ```data-result``` JSON block in its closing comment.
"""

from __future__ import annotations

import json
import os
import re
import smtplib
import ssl
import urllib.error
import urllib.request
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path

AGENT_ROOT = Path(__file__).resolve().parent.parent
PENDING_PATH = AGENT_ROOT / "logs" / "_pending.json"

AGENT_REPO = os.environ.get("AGENT_REPO", "dh914/Agent")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")

RESULT_FENCE = re.compile(r"```data-result\s*\n(.*?)```", re.S)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _gh(method: str, path: str) -> object:
    req = urllib.request.Request(f"https://api.github.com{path}", method=method)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    if GITHUB_TOKEN:
        req.add_header("Authorization", f"Bearer {GITHUB_TOKEN}")
    with urllib.request.urlopen(req, timeout=20) as resp:
        raw = resp.read()
    return json.loads(raw) if raw else None


def load_pending() -> dict:
    if PENDING_PATH.exists():
        try:
            return json.loads(PENDING_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {"pending": [], "relayed": []}


def save_pending(state: dict) -> None:
    PENDING_PATH.parent.mkdir(parents=True, exist_ok=True)
    PENDING_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def find_result(issue_number: int) -> dict | None:
    """Return the parsed ``data-result`` JSON block, or None if not ready."""
    try:
        issue = _gh("GET", f"/repos/{AGENT_REPO}/issues/{issue_number}")
    except urllib.error.HTTPError as exc:
        print(f"[relay] cannot read issue #{issue_number}: {exc}")
        return None
    if not isinstance(issue, dict) or issue.get("state") != "closed":
        return None
    try:
        comments = _gh("GET", f"/repos/{AGENT_REPO}/issues/{issue_number}/comments") or []
    except urllib.error.HTTPError as exc:
        print(f"[relay] cannot read comments for #{issue_number}: {exc}")
        return None
    for comment in reversed(comments if isinstance(comments, list) else []):
        body = comment.get("body") or ""
        match = RESULT_FENCE.search(body)
        if not match:
            continue
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
    return None


def format_email(entry: dict, result: dict) -> tuple[str, str]:
    subject = f"Re: {entry.get('subject') or ''} — 처리 결과"
    fetched = result.get("fetched") or []
    processed = result.get("processed") or []
    errors = result.get("errors") or []
    lines = [
        f"안녕하세요, 요청하신 작업의 처리 결과를 회신드립니다 ({_utc_now()}).",
        "",
        f"요청 ID: {entry.get('request_id')}",
        f"작업 티켓: {entry.get('issue_url')}",
        "",
        f"- 처리 파일: {len(processed)}건",
        f"- 다운로드 자원: {len(fetched)}건",
        f"- 오류: {len(errors)}건",
    ]
    if processed:
        lines += ["", "처리된 파일:"]
        for p in processed:
            lines.append(
                f"  - {p.get('name')} | {p.get('bytes')} bytes | "
                f"lines={p.get('line_count')} | sha256={(p.get('sha256') or '')[:12]}…"
            )
    if errors:
        lines += ["", "오류:"]
        for e in errors:
            lines.append(f"  - {e}")
    lines += [
        "",
        "상세 결과는 첨부된 JSON 파일을 참고하세요.",
        "",
        "감사합니다.",
    ]
    return subject, "\n".join(lines)


def send_email(to_addr: str, subject: str, body: str, attachment: dict) -> bool:
    host = os.environ.get("SMTP_HOST")
    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASS")
    if not (host and user and password and to_addr):
        print(f"[relay] SMTP creds or recipient missing; skipping send to {to_addr!r}.")
        return False
    msg = EmailMessage()
    msg["From"] = user
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg.set_content(body)
    payload = json.dumps(attachment, ensure_ascii=False, indent=2).encode("utf-8")
    msg.add_attachment(payload, maintype="application", subtype="json", filename="data-result.json")
    context = ssl.create_default_context()
    with smtplib.SMTP_SSL(host, 465, context=context) as smtp:
        smtp.login(user, password)
        smtp.send_message(msg)
    return True


def main() -> int:
    state = load_pending()
    pending = state.get("pending", [])
    if not pending:
        print("[relay] no pending dispatches.")
        return 0

    still_pending: list[dict] = []
    relayed_now = 0
    for entry in pending:
        issue_number = entry.get("issue_number")
        if not isinstance(issue_number, int):
            still_pending.append(entry)
            continue
        result = find_result(issue_number)
        if result is None:
            still_pending.append(entry)
            continue
        subject, body = format_email(entry, result)
        try:
            sent = send_email(entry.get("reply_to") or "", subject, body, result)
        except Exception as exc:
            print(f"[relay] send failed for #{issue_number}: {exc}")
            still_pending.append(entry)
            continue
        state.setdefault("relayed", []).append(
            {**entry, "relayed_at": _utc_now(), "sent": sent}
        )
        relayed_now += int(sent)
        print(f"[relay] #{issue_number} → {entry.get('reply_to')}: sent={sent}")

    state["pending"] = still_pending
    save_pending(state)
    print(f"[relay] relayed {relayed_now} result(s); {len(still_pending)} still pending.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
