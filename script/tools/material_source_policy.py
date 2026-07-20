from __future__ import annotations

from typing import Iterable

POLICY_NAME = "material_source_capability_policy_v1"

FORBIDDEN_CAPABILITIES = frozenset(
    {
        "anti_bot_bypass",
        "automated_login_session",
        "js_signature_injection",
        "cdn_watermark_bypass",
        "watermark_removal",
        "captcha_bypass",
    }
)

SAFE_CAPABILITIES = frozenset(
    {
        "metadata_probe",
        "user_provided_url_import",
        "authorized_cookie_use",
        "public_page_crawl",
        "rendered_dom_extraction",
        "browser_network_observation",
        "user_authorized_browser_session",
        "media_response_download",
        "format_standardization",
        "manual_smoke_test",
    }
)


def is_capability_allowed(capability: str) -> bool:
    """Return whether a declared material-source capability is policy-allowed."""
    normalized = str(capability or "").strip()
    return normalized in SAFE_CAPABILITIES and normalized not in FORBIDDEN_CAPABILITIES


def validate_material_source_capabilities(
    adapter_name: str,
    capabilities: Iterable[str],
) -> dict:
    allowed_capabilities: list[str] = []
    blocked_capabilities: list[str] = []

    for capability in capabilities:
        normalized = str(capability or "").strip()
        if not normalized:
            continue
        if is_capability_allowed(normalized):
            allowed_capabilities.append(normalized)
        else:
            blocked_capabilities.append(normalized)

    return {
        "status": "blocked" if blocked_capabilities else "allowed",
        "adapter_name": adapter_name,
        "allowed_capabilities": allowed_capabilities,
        "blocked_capabilities": blocked_capabilities,
        "policy": POLICY_NAME,
    }
