"""Lightweight tests for the hourly email routine — no external services."""

from __future__ import annotations

import json
import sys
import unittest
from email.message import EmailMessage
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import email_routine  # noqa: E402
import relay_results  # noqa: E402


class RoutineTests(unittest.TestCase):
    def test_dry_run_fetch_when_no_credentials(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=True):
            messages = email_routine.fetch_unread_emails()
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["uid"], "dry-run")
        self.assertEqual(messages[0]["attachments"], [])

    def test_needs_data_detects_korean_english_and_attachments(self) -> None:
        self.assertTrue(email_routine.needs_data({"subject": "주간 데이터 요청", "body": ""}))
        self.assertTrue(email_routine.needs_data({"subject": "", "body": "please share the dataset"}))
        self.assertFalse(email_routine.needs_data({"subject": "lunch?", "body": "see you at 12"}))
        self.assertTrue(
            email_routine.needs_data(
                {"subject": "hi", "body": "", "attachments": [{"name": "a.csv", "bytes": b"x"}]}
            )
        )

    def test_extract_attachments_pulls_files_from_multipart(self) -> None:
        msg = EmailMessage()
        msg["From"] = "a@b.c"
        msg["Subject"] = "x"
        msg.set_content("hello body")
        msg.add_attachment(b"id,val\n1,2\n", maintype="text", subtype="csv", filename="data.csv")
        atts = email_routine._extract_attachments(msg)
        self.assertEqual(len(atts), 1)
        self.assertEqual(atts[0]["name"], "data.csv")
        self.assertIn(b"id,val", atts[0]["bytes"])

    def test_persist_attachments_writes_and_returns_manifest(self) -> None:
        attachments = [{"name": "sample.txt", "bytes": b"hello\nworld\n"}]
        manifest = email_routine.persist_attachments("test-req-id", attachments)
        try:
            self.assertEqual(len(manifest), 1)
            self.assertEqual(manifest[0]["path"], "incoming/test-req-id/sample.txt")
            self.assertEqual(manifest[0]["bytes"], 12)
            self.assertTrue((email_routine.INCOMING_DIR / "test-req-id" / "sample.txt").exists())
        finally:
            target = email_routine.INCOMING_DIR / "test-req-id"
            for p in target.glob("*"):
                p.unlink()
            target.rmdir()

    def test_build_instruction_spec_includes_metadata_and_paths(self) -> None:
        spec = email_routine.build_instruction_spec(
            "rid", "x@y.z", "my-out", [{"path": "incoming/rid/a.txt"}]
        )
        self.assertIn("request_id: rid", spec)
        self.assertIn("reply_to: x@y.z", spec)
        self.assertIn("output: my-out", spec)
        self.assertIn("  - incoming/rid/a.txt", spec)

    def test_draft_reply_mentions_data_issue_and_attachments(self) -> None:
        body = email_routine.draft_reply(
            {"subject": "x"},
            "https://example/issue/1",
            [{"name": "a.csv", "bytes": 10}],
        )
        self.assertIn("https://example/issue/1", body)
        self.assertIn("a.csv", body)

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


class RelayTests(unittest.TestCase):
    def test_format_email_lists_processed_files(self) -> None:
        entry = {
            "request_id": "abc",
            "issue_url": "https://example/i/1",
            "subject": "weekly stats",
        }
        result = {
            "processed": [
                {"name": "a.csv", "bytes": 12, "line_count": 2, "sha256": "0" * 64},
            ],
            "fetched": [],
            "errors": [],
        }
        subject, body = relay_results.format_email(entry, result)
        self.assertIn("처리 결과", subject)
        self.assertIn("a.csv", body)
        self.assertIn("abc", body)

    def test_result_fence_regex_extracts_json(self) -> None:
        body = "prefix\n```data-result\n{\"ok\": true}\n```\nsuffix"
        match = relay_results.RESULT_FENCE.search(body)
        self.assertIsNotNone(match)
        self.assertEqual(json.loads(match.group(1)), {"ok": True})


if __name__ == "__main__":
    unittest.main()
