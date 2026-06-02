"""Hourly email routine: check → reply/collaborate → record.

Responsibilities by repository:
- Agent (this repo): IMAP fetch, draft + send replies, write per-run log,
  persist incoming attachments and open a data-request issue on this repo
  so the Data repo's hourly routine can process them.
- Data (dh914/Data): produce/fetch data needed to answer tasks raised in mail,
  including processing attachments saved by Agent.
- System (dh914/System): orchestrate + persist the cross-repo audit trail.
"""

from __future__ import annotations

import email
import hashlib
import imaplib
import json
import os
import re
import smtplib
import ssl
import uuid
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import parseaddr
from pathlib import Path
from typing import Iterable

import urllib.request

# Load .env when running locally (not in CI where secrets come from env)
_env_file = Path(__file__).resolve().parent.parent / ".env"
if _env_file.exists():
    for _line in _env_file.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip())

AGENT_ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = AGENT_ROOT / "logs"
INCOMING_DIR = AGENT_ROOT / "incoming"
PENDING_PATH = LOG_DIR / "_pending.json"
LOG_DIR.mkdir(parents=True, exist_ok=True)

AGENT_REPO = os.environ.get("AGENT_REPO", "dh914/Agent")
AGENT_BRANCH = os.environ.get("AGENT_BRANCH", "main")
DATA_REPO = os.environ.get("DATA_REPO", "dh914/Data")
SYSTEM_REPO = os.environ.get("SYSTEM_REPO", "dh914/System")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _safe_name(value: str, fallback: str) -> str:
    cleaned = _SAFE_NAME.sub("-", value).strip("-")
    return cleaned[:80] or fallback


def fetch_unread_emails() -> list[dict]:
    host = os.environ.get("IMAP_HOST")
    user = os.environ.get("IMAP_USER")
    password = os.environ.get("IMAP_PASS")
    if not (host and user and password):
        print("[fetch] IMAP credentials missing; emitting dry-run heartbeat message.")
        return [
            {
                "uid": "dry-run",
                "from": "noreply@local.dry-run",
                "subject": "[dry-run] hourly routine heartbeat",
                "date": _utc_now(),
                "body": (
                    "This entry is generated when IMAP credentials are not configured. "
                    "It proves the hourly cron is firing and the script is executing end-to-end. "
                    "Configure IMAP_HOST/USER/PASS to replace this with real inbox traffic."
                ),
                "attachments": [],
            }
        ]

    messages: list[dict] = []
    port = int(os.environ.get("IMAP_PORT", "993"))
    with imaplib.IMAP4_SSL(host, port) as imap:
        imap.login(user, password)
        imap.select("INBOX")
        status, data = imap.search(None, "UNSEEN")
        if status != "OK":
            return []
        for num in data[0].split():
            status, msg_data = imap.fetch(num, "(RFC822)")
            if status != "OK":
                continue
            raw = msg_data[0][1]
            msg = email.message_from_bytes(raw)
            messages.append(
                {
                    "uid": num.decode(),
                    "from": parseaddr(msg.get("From", ""))[1],
                    "subject": msg.get("Subject", ""),
                    "date": msg.get("Date", ""),
                    "body": _extract_body(msg),
                    "attachments": _extract_attachments(msg),
                }
            )
    return messages


def _extract_body(msg: email.message.Message) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and "attachment" not in (
                part.get("Content-Disposition") or ""
            ).lower():
                payload = part.get_payload(decode=True) or b""
                return payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        return ""
    payload = msg.get_payload(decode=True) or b""
    return payload.decode(msg.get_content_charset() or "utf-8", errors="replace")


def _extract_attachments(msg: email.message.Message) -> list[dict]:
    """Return [{name, bytes}] for each attached part (skipping inline text bodies)."""
    if not msg.is_multipart():
        return []
    out: list[dict] = []
    seen = 0
    for part in msg.walk():
        disposition = (part.get("Content-Disposition") or "").lower()
        filename = part.get_filename()
        if not filename and "attachment" not in disposition:
            continue
        payload = part.get_payload(decode=True)
        if not payload:
            continue
        seen += 1
        name = _safe_name(filename or f"part-{seen}", f"part-{seen}")
        out.append({"name": name, "bytes": payload})
    return out


def needs_data(message: dict) -> bool:
    if message.get("attachments"):
        return True
    body = (message.get("body") or "").lower()
    subject = (message.get("subject") or "").lower()
    triggers = ("data", "데이터", "dataset", "report", "분석", "통계", "파일", "처리")
    return any(t in body or t in subject for t in triggers)


def persist_attachments(request_id: str, attachments: list[dict]) -> list[dict]:
    """Write attachments to ``incoming/<request_id>/`` and return manifest entries."""
    if not attachments:
        return []
    dest = INCOMING_DIR / request_id
    dest.mkdir(parents=True, exist_ok=True)
    manifest: list[dict] = []
    for att in attachments:
        path = dest / att["name"]
        path.write_bytes(att["bytes"])
        manifest.append(
            {
                "name": att["name"],
                "path": f"incoming/{request_id}/{att['name']}",
                "bytes": len(att["bytes"]),
                "sha256": hashlib.sha256(att["bytes"]).hexdigest(),
            }
        )
    return manifest


def build_instruction_spec(request_id: str, reply_to: str, output: str, attachments: list[dict]) -> str:
    lines = [
        f"request_id: {request_id}",
        f"reply_to: {reply_to}",
        f"output: {output}",
        f"source_repo: {AGENT_REPO}",
        f"source_branch: {AGENT_BRANCH}",
    ]
    if attachments:
        lines.append("attachments:")
        for a in attachments:
            lines.append(f"  - {a['path']}")
    return "\n".join(lines)


def open_data_request(message: dict, manifest: list[dict], request_id: str) -> tuple[int | None, str | None]:
    """Open a ``data-request`` issue on the Agent repo for the Data routine to poll.

    Returns (issue_number, html_url). The Data hourly routine reads issues
    from this repo (see Data/README.md), so the issue must live here.
    """
    output_slug = _safe_name(message.get("subject") or "", f"req-{request_id}")
    spec = build_instruction_spec(request_id, message.get("from") or "", output_slug, manifest)

    body = (
        f"Triggered by email from `{message.get('from')}` at {message.get('date')}.\n\n"
        f"```yaml\n{spec}\n```\n\n"
        f"### Original message\n\n```\n{(message.get('body') or '')[:4000]}\n```"
    )

    if not GITHUB_TOKEN:
        print("[data] no GITHUB_TOKEN; would have opened an Agent repo data-request issue.")
        return None, None

    payload = json.dumps(
        {
            "title": f"[data-request] {message.get('subject') or '(no subject)'}",
            "body": body,
            "labels": ["data-request", "email-routine"],
        }
    ).encode()
    req = urllib.request.Request(
        f"https://api.github.com/repos/{AGENT_REPO}/issues",
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read())
        return data.get("number"), data.get("html_url")
    except Exception as exc:
        print(f"[data] failed to open issue: {exc}")
        return None, None


def load_pending() -> dict:
    if PENDING_PATH.exists():
        try:
            return json.loads(PENDING_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {"pending": [], "relayed": []}


def save_pending(state: dict) -> None:
    PENDING_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def record_pending(entry: dict) -> None:
    state = load_pending()
    state["pending"].append(entry)
    save_pending(state)


def draft_reply(message: dict, data_issue_url: str | None, manifest: list[dict]) -> str:
    lines = [
        f"안녕하세요, 메일 잘 받았습니다 ({_utc_now()}).",
        "",
        "요청 주신 내용은 자동 루틴이 접수했으며, 1시간 단위 사이클에서 처리됩니다.",
    ]
    if manifest:
        lines += [
            "",
            f"첨부 파일 {len(manifest)}건을 수신하여 처리 대기열에 등록했습니다:",
        ]
        for a in manifest:
            lines.append(f"  - {a['name']} ({a['bytes']} bytes)")
    if data_issue_url:
        lines += [
            "",
            f"데이터 확보/분석은 작업 티켓으로 등록되었습니다: {data_issue_url}",
        ]
    lines += [
        "",
        "처리 결과가 준비되면 다시 회신드리겠습니다.",
        "",
        "감사합니다.",
    ]
    return "\n".join(lines)


def send_reply(message: dict, body: str) -> bool:
    host = os.environ.get("SMTP_HOST")
    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASS")
    if message.get("uid") == "dry-run":
        print("[reply] dry-run message; recording drafted reply without sending.")
        return False
    if not (host and user and password and message.get("from")):
        print("[reply] SMTP credentials or recipient missing; skipping send.")
        return False
    reply = EmailMessage()
    reply["From"] = user
    reply["To"] = message["from"]
    reply["Subject"] = f"Re: {message.get('subject') or ''}"
    reply.set_content(body)
    context = ssl.create_default_context()
    with smtplib.SMTP_SSL(host, 465, context=context) as smtp:
        smtp.login(user, password)
        smtp.send_message(reply)
    return True


def record_run(entries: Iterable[dict]) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log_path = LOG_DIR / f"{stamp}.json"
    log_path.write_text(
        json.dumps({"ran_at": _utc_now(), "entries": list(entries)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return log_path


def main() -> int:
    messages = fetch_unread_emails()
    entries: list[dict] = []
    for msg in messages:
        manifest: list[dict] = []
        issue_number: int | None = None
        issue_url: str | None = None
        request_id: str | None = None
        if needs_data(msg):
            request_id = uuid.uuid4().hex[:12]
            manifest = persist_attachments(request_id, msg.get("attachments") or [])
            issue_number, issue_url = open_data_request(msg, manifest, request_id)
            if issue_number is not None and msg.get("from"):
                record_pending(
                    {
                        "request_id": request_id,
                        "issue_number": issue_number,
                        "issue_url": issue_url,
                        "reply_to": msg.get("from"),
                        "subject": msg.get("subject"),
                        "opened_at": _utc_now(),
                    }
                )

        body = draft_reply(msg, issue_url, manifest)
        sent = False
        try:
            sent = send_reply(msg, body)
        except Exception as exc:
            print(f"[reply] send failed: {exc}")
        entries.append(
            {
                "from": msg.get("from"),
                "subject": msg.get("subject"),
                "needs_data": issue_url is not None or bool(manifest),
                "data_issue": issue_url,
                "data_issue_number": issue_number,
                "request_id": request_id,
                "attachments": [{k: a[k] for k in ("name", "bytes", "sha256")} for a in manifest],
                "replied": sent,
                "drafted_reply": body,
                "uid": msg.get("uid"),
            }
        )
    log_path = record_run(entries)
    print(f"[record] wrote {log_path} with {len(entries)} entries.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
