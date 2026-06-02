"""Unit tests for the GitHub OTP parser. No network."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import github_otp  # noqa: E402


class ExtractCodeTests(unittest.TestCase):
    def test_six_digit_verification_code(self) -> None:
        body = "Your GitHub verification code is 482913. It expires in 10 minutes."
        self.assertEqual(github_otp.extract_code(body), "482913")

    def test_hyphenated_code(self) -> None:
        body = "verification code: 123-4567 do not share"
        self.assertEqual(github_otp.extract_code(body), "1234567")

    def test_inverted_phrasing(self) -> None:
        body = "390210 is your GitHub sign-in verification code."
        self.assertEqual(github_otp.extract_code(body), "390210")

    def test_returns_none_when_absent(self) -> None:
        self.assertIsNone(github_otp.extract_code("welcome to github!"))


class ExtractDeviceLinkTests(unittest.TestCase):
    def test_device_login_link(self) -> None:
        body = "Verify here: https://github.com/login/device?user_code=ABCD-1234 thanks"
        self.assertEqual(
            github_otp.extract_device_link(body),
            "https://github.com/login/device?user_code=ABCD-1234",
        )

    def test_no_link(self) -> None:
        self.assertIsNone(github_otp.extract_device_link("no link here"))


class SenderFilterTests(unittest.TestCase):
    def test_known_senders_pass(self) -> None:
        for addr in (
            "noreply@github.com",
            "billing@github.com",
            "anything@github.com",
        ):
            self.assertTrue(github_otp._is_from_github(addr))

    def test_lookalike_rejected(self) -> None:
        self.assertFalse(github_otp._is_from_github("github@evil.example"))
        self.assertFalse(github_otp._is_from_github("noreply@github.com.attacker.tld"))


class MaskTests(unittest.TestCase):
    def test_mask_preserves_ends(self) -> None:
        self.assertEqual(github_otp._mask("482913"), "4****3")

    def test_mask_short_value(self) -> None:
        self.assertEqual(github_otp._mask("12"), "**")


if __name__ == "__main__":
    unittest.main()
