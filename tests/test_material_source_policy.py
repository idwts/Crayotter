from __future__ import annotations

import unittest
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "script"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))


class MaterialSourcePolicyTests(unittest.TestCase):
    def test_forbidden_capability_is_blocked_without_exception(self) -> None:
        from script.tools.material_source_policy import validate_material_source_capabilities

        result = validate_material_source_capabilities(
            "risky_adapter",
            ["anti_bot_bypass"],
        )

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["adapter_name"], "risky_adapter")
        self.assertEqual(result["allowed_capabilities"], [])
        self.assertEqual(result["blocked_capabilities"], ["anti_bot_bypass"])
        self.assertEqual(result["policy"], "material_source_capability_policy_v1")

    def test_safe_capabilities_are_allowed(self) -> None:
        from script.tools.material_source_policy import (
            is_capability_allowed,
            validate_material_source_capabilities,
        )

        capabilities = [
            "metadata_probe",
            "user_provided_url_import",
            "authorized_cookie_use",
            "format_standardization",
            "manual_smoke_test",
        ]

        result = validate_material_source_capabilities("safe_adapter", capabilities)

        self.assertEqual(result["status"], "allowed")
        self.assertEqual(result["allowed_capabilities"], capabilities)
        self.assertEqual(result["blocked_capabilities"], [])
        for capability in capabilities:
            with self.subTest(capability=capability):
                self.assertTrue(is_capability_allowed(capability))

    def test_mixed_capabilities_return_allowed_and_blocked_groups(self) -> None:
        from script.tools.material_source_policy import validate_material_source_capabilities

        result = validate_material_source_capabilities(
            "mixed_adapter",
            [
                "metadata_probe",
                "watermark_removal",
                "format_standardization",
                "captcha_bypass",
            ],
        )

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(
            result["allowed_capabilities"],
            ["metadata_probe", "format_standardization"],
        )
        self.assertEqual(
            result["blocked_capabilities"],
            ["watermark_removal", "captcha_bypass"],
        )

    def test_unknown_capability_is_blocked_by_default(self) -> None:
        from script.tools.material_source_policy import (
            is_capability_allowed,
            validate_material_source_capabilities,
        )

        result = validate_material_source_capabilities(
            "unknown_adapter",
            ["metadata_probe", "experimental_scraper"],
        )

        self.assertFalse(is_capability_allowed("experimental_scraper"))
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["allowed_capabilities"], ["metadata_probe"])
        self.assertEqual(result["blocked_capabilities"], ["experimental_scraper"])


if __name__ == "__main__":
    unittest.main()
