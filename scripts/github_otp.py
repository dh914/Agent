"""Fetch the latest GitHub verification code from an IMAP inbox.

Designed for one-shot, manual use (`workflow_dispatch`) — NOT a scheduled job.
Reads `IMAP_HOST` / `IMAP_USER` / `IMAP_PASS` from the environment so credentials
never live in source. For iCloud:

    IMAP_HOST=imap.mail.me.com
    IMAP_USER=<your-apple-id>@icloud.com
    IMAP_PASS=<app-specific password from https://account.apple.com>

Output: prints a JSON object with the extracted code (and/or device-verification
link) plus the message metadata. The script masks the code in stdout when
`--mask` is passed; the unmasked code is written to `$GITHUB_OUTPUT` when that
variable is set, so the workflow can consume it without echoing it to logs.
"""

from __future__ import annotations

import argparse
import email
import imaplib
import json
import os
import re
import sys
from email.utils import parseaddr, parsedate_to_datetime
from typing import Iterable

GITHUB_SENDERS = (
    "noreply@github.com",
    "notifications@github.com",
    "billing@github.com",
    "security@github.com",
)

# GitHub's transactional codes are 6–8 digit numeric tokens. Sometimes they
# arrive with a separator (e.g. "123-456"); we strip it before reporting.
CODE_PATTERNS = (
    re.compile(r"verification code[^0-9]{0,40}([0-9]{3}[\s-]?[0-9]{3,5})", re.I),
    re.compile(r"one-time (?:password|code)[^0-9]{0,40}([0-9]{6,8})", re.I),
    re.compile(r"\b([0-9]{6,8})\b\s+is your.*?(?:verification|security|sign-?in)", re.I),
)

DEVICE_LINK_PATTERN = re.compile(
    r"https://github\.com/(?:login/device|sessions/verified-device)\S*", re.I
)


def _decode(payload: bytes, charset: str | None) -> str:
    return payload.decode(charset or "utf-8", errors="replace")


def _extract_text(msg: email.message.Message) -> str:
    if msg.is_multipart():
        plain: list[str] = []
        html_fallback: list[str] = []
        for part in msg.walk():
            ctype = part.get_content_type()
            payload = part.get_payload(decode=True)
            if not payload:
                continue
            text = _decode(payload, part.get_content_charset())
            if ctype == "text/plain":
                plain.append(text)
            elif ctype == "text/html":
                html_fallback.append(text)
        return "\n".join(plain) if plain else "\n".join(html_fallback)
    payload = msg.get_payload(decode=True) or b""
    return _decode(payload, msg.get_content_charset())


def extract_code(text: str) -> str | None:
    for pattern in CODE_PATTERNS:
        match = pattern.search(text)
        if match:
            return re.sub(r"[\s-]", "", match.group(1))
    return None


def extract_device_link(text: str) -> str | None:
    match = DEVICE_LINK_PATTERN.search(text)
    return match.group(0) if match else None


def _is_from_github(addr: str) -> bool:
    addr = addr.lower()
    if any(addr == s for s in GITHUB_SENDERS):
        return True
    return addr.endswith("@github.com")


def fetch_latest_github_message(
    host: str, user: str, password: str, lookback: int = 25
) -> dict | None:
    """Return the most recent GitHub-origin message in INBOX, or None."""
    with imaplib.IMAP4_SSL(host) as imap:
        imap.login(user, password)
        imap.select("INBOX")
        # Search across recent mail; iCloud honors RFC3501 SEARCH but is picky
        # about FROM filters, so we fetch recent UIDs and filter locally.
        status, data = imap.search(None, "ALL")
        if status != "OK" or not data or not data[0]:
            return None
        uids = data[0].split()[-lookback:][::-1]
        for uid in uids:
            status, msg_data = imap.fetch(uid, "(RFC822)")
            if status != "OK" or not msg_data or not msg_data[0]:
                continue
            raw = msg_data[0][1]
            msg = email.message_from_bytes(raw)
            sender = parseaddr(msg.get("From", ""))[1]
            if not _is_from_github(sender):
                continue
            body = _extract_text(msg)
            return {
                "uid": uid.decode(),
                "from": sender,
                "subject": msg.get("Subject", ""),
                "date": msg.get("Date", ""),
                "received_at": _safe_date(msg.get("Date", "")),
                "code": extract_code(body),
                "device_link": extract_device_link(body),
                "snippet": _snippet(body),
            }
    return None


def _safe_date(value: str) -> str | None:
    try:
        return parsedate_to_datetime(value).isoformat()
    except (TypeError, ValueError):
        return None


def _snippet(body: str, limit: int = 240) -> str:
    collapsed = re.sub(r"\s+", " ", body).strip()
    return collapsed[:limit]


def _mask(code: str) -> str:
    if len(code) <= 2:
        return "*" * len(code)
    return code[0] + "*" * (len(code) - 2) + code[-1]


def _write_github_output(name: str, value: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as fp:
        fp.write(f"{name}={value}\n")


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mask",
        action="store_true",
        help="Replace the digits of the code with * in stdout (still written to $GITHUB_OUTPUT).",
    )
    parser.add_argument(
        "--lookback",
        type=int,
        default=25,
        help="Scan this many most-recent messages for a GitHub sender.",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    host = os.environ.get("IMAP_HOST")
    user = os.environ.get("IMAP_USER")
    password = os.environ.get("IMAP_PASS")
    if not (host and user and password):
        print(
            json.dumps({"error": "IMAP_HOST/USER/PASS not configured"}),
            file=sys.stderr,
        )
        return 2

    message = fetch_latest_github_message(host, user, password, lookback=args.lookback)
    if not message:
        print(json.dumps({"found": False}))
        return 1

    if message.get("code"):
        _write_github_output("code", message["code"])
    if message.get("device_link"):
        _write_github_output("device_link", message["device_link"])

    display = dict(message)
    if args.mask and display.get("code"):
        display["code"] = _mask(display["code"])
    print(json.dumps(display, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
