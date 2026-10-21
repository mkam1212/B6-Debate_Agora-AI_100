"""Validated avatar accents shared by account and participant persistence."""
import re

DEFAULT_AVATAR_COLOR = "#2563eb"


def normalize_avatar_color(value):
    if not isinstance(value, str) or re.fullmatch(r"#[0-9a-fA-F]{6}", value) is None:
        raise ValueError("avatarColor must be a six-digit hex color.")
    return value.lower()
