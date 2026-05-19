"""Lightweight tests for the hourly email routine — no external services."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import email_routine  # noqa: E402


class RoutineTests(unittest.TestCase):
    def test_dry_run_fetch_when_no_credentials(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=True):
            messages = email_routine.fetch_unread_emails()
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["uid"], "dry-run")

    def test_needs_data_detects_korean_and_english(self) -> None:
        self.assertTrue(email_routine.needs_data({"subject": "주간 데이터 요청", "body": ""}))
        self.assertTrue(email_routine.needs_data({"subject": "", "body": "please share the dataset"}))
        self.assertFalse(email_routine.needs_data({"subject": "lunch?", "body": "see you at 12"}))

    def test_draft_reply_mentions_data_issue_url(self) -> None:
        body = email_routine.draft_reply({"subject": "x"}, "https://example/issue/1")
        self.assertIn("https://example/issue/1", body)
        self.assertIn("1시간", body)

    def test_send_reply_skips_dry_run_entries(self) -> None:
        with mock.patch.dict(
            "os.environ",
            {"SMTP_HOST": "h", "SMTP_USER": "u", "SMTP_PASS": "p"},
            clear=True,
        ):
            sent = email_routine.send_reply({"uid": "dry-run", "from": "x@y"}, "hi")
        self.assertFalse(sent)

    def test_record_run_writes_json(self) -> None:
        path = email_routine.record_run([{"a": 1}])
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["entries"], [{"a": 1}])
            self.assertIn("ran_at", payload)
        finally:
            path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
