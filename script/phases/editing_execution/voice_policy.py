from __future__ import annotations


def resolve_narration_voice(*texts: str, default: str = "Ethan") -> str:
    """Map an explicit user request to a supported deterministic voice."""

    combined = " ".join(str(item or "") for item in texts).lower()
    if any(
        marker in combined for marker in ("serena", "温柔女", "柔和女", "gentle female")
    ):
        return "Serena"
    if any(
        marker in combined
        for marker in (
            "female",
            "woman",
            "women",
            "女声",
            "女性",
            "女旁白",
            "小姐姐",
            "cherry",
            "chelsie",
        )
    ):
        return "Cherry"
    if any(marker in combined for marker in ("male", "man", "男声", "男性", "男旁白")):
        return "Ethan"
    return default
