"""Hourly email routine: check → reply/collaborate → record.

Responsibilities by repository:
- Agent (this repo): IMAP fetch, draft + send replies, write per-run log.
- Data (dh914/Data): produce/fetch data needed to answer tasks raised in mail.
- System (dh914/System): orchestrate + persist the cross-repo audit trail.
"""

from __future__ import annotations

import email
import imaplib
import json
import os
import smtplib
import ssl
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import parseaddr
from pathlib import Path
from typing import Iterable

import urllib.request

AGENT_ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = AGENT_ROOT / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

DATA_REPO = os.environ.get("DATA_REPO", "dh914/Data")
SYSTEM_REPO = os.environ.get("SYSTEM_REPO", "dh914/System")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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
            }
        ]

    messages: list[dict] = []
    with imaplib.IMAP4_SSL(host) as imap:
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
                }
            )
    return messages


def _extract_body(msg: email.message.Message) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True) or b""
                return payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        return ""
    payload = msg.get_payload(decode=True) or b""
    return payload.decode(msg.get_content_charset() or "utf-8", errors="replace")


def needs_data(message: dict) -> bool:
    body = (message.get("body") or "").lower()
    subject = (message.get("subject") or "").lower()
    triggers = ("data", "데이터", "dataset", "report", "분석", "통계")
    return any(t in body or t in subject for t in triggers)


def open_data_request(message: dict) -> str | None:
    """Open an issue in the Data repo so a downstream worker fulfills it."""
    if not GITHUB_TOKEN:
        print("[data] no GITHUB_TOKEN; would have opened a Data repo issue for this message.")
        return None
    payload = json.dumps(
        {
            "title": f"[data-request] {message.get('subject') or '(no subject)'}",
            "body": (
                f"Triggered by email from `{message.get('from')}` at {message.get('date')}.\n\n"
                f"```\n{(message.get('body') or '')[:4000]}\n```"
            ),
            "labels": ["data-request", "email-routine"],
        }
    ).encode()
    req = urllib.request.Request(
        f"https://api.github.com/repos/{DATA_REPO}/issues",
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
            return json.loads(resp.read()).get("html_url")
    except Exception as exc:
        print(f"[data] failed to open issue: {exc}")
        return None


def draft_reply(message: dict, data_issue_url: str | None) -> str:
    lines = [
        f"안녕하세요, 메일 잘 받았습니다 ({_utc_now()}).",
        "",
        "요청 주신 내용은 자동 루틴이 접수했으며, 1시간 단위 사이클에서 처리됩니다.",
    ]
    if data_issue_url:
        lines += [
            "",
            f"데이터 확보/분석은 Data 저장소에 작업 티켓으로 등록되었습니다: {data_issue_url}",
        ]
    lines += [
        "",
        "처리 결과는 회신 메일과 System 저장소 기록으로 다시 안내드리겠습니다.",
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
        data_url = open_data_request(msg) if needs_data(msg) else None
        body = draft_reply(msg, data_url)
        sent = False
        try:
            sent = send_reply(msg, body)
        except Exception as exc:
            print(f"[reply] send failed: {exc}")
        entries.append(
            {
                "from": msg.get("from"),
                "subject": msg.get("subject"),
                "needs_data": data_url is not None,
                "data_issue": data_url,
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
