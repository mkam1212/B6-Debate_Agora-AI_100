"""Shared branded transactional emails; no remote images or browser assets required."""
import base64
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from flask import current_app, render_template

from .config import scoped_path


@lru_cache(maxsize=8)
def _asset_content(path, modified_at):
    return base64.b64encode(Path(path).read_bytes()).decode("ascii")


def _brand():
    origin = current_app.config.get("PUBLIC_APP_URL") or "https://debate-agora.beringtech.com"
    parsed = urlsplit(origin)
    if parsed.scheme not in ("https", "http") or not parsed.hostname or parsed.username or parsed.password:
        origin = "https://debate-agora.beringtech.com"
    assets = Path(current_app.config["STATIC_ROOT"]) / "app/text/assets"
    logo = assets / "debate-agora-wordmark.png"
    banner = assets / "invite-banner-selected-blue-v5-email.jpg"
    return {
        "logo_src": "cid:agora-wordmark",
        "banner_src": "cid:agora-banner",
        "app_url": origin.rstrip("/") + scoped_path(current_app.config["URL_PREFIX"], "/"),
        "site_label": urlsplit(origin).netloc,
    }, [{
        "filename": "debate-agora.png", "content_type": "image/png",
        "content_id": "agora-wordmark", "content": _asset_content(str(logo), logo.stat().st_mtime_ns),
    }, {
        "filename": "agora-banner.jpg", "content_type": "image/jpeg",
        "content_id": "agora-banner", "content": _asset_content(str(banner), banner.stat().st_mtime_ns),
    }]


def verification_email_content(code, purpose):
    variants = {
        "register": ("Verify your email.", "Enter this code to create your Debate Agora account.", "create your account"),
        "email": ("Confirm your new email.", "Enter this code to update your email address.", "change your email"),
        "reset": ("Reset your password.", "Enter this code to choose a new password.", "reset your password"),
    }
    heading, intro, action = variants[purpose]
    subject = f"{code} — Debate Agora verification"
    brand, attachments = _brand()
    return {
        "subject": subject,
        "text": f"Your Debate Agora code is {code}. Use it to {action}. It expires in 10 minutes. If you did not request this, ignore this email.",
        "html": render_template("emails/verification.html", **brand, subject=subject,
                                preheader="Your verification code. Valid for 10 minutes.",
                                heading=heading, intro=intro, code=code),
        "attachments": attachments,
    }


def invitation_email_content(inviter_name, invite_url, reward):
    subject = "You’re invited to Debate Agora"
    brand, attachments = _brand()
    return {
        "subject": subject,
        "text": f"{inviter_name} invited you to Debate Agora.\n\nThink through questions with different AI perspectives.\n\nJoin the conversation: {invite_url}\n\nCreate an account and verify your email to get {reward} extra AI replies for both of you. If you weren’t expecting this invitation, you can ignore it.",
        "html": render_template("emails/invitation.html", **brand, subject=subject,
                                preheader=f"{inviter_name} invited you. You both get {reward} extra AI replies.",
                                inviter_name=inviter_name, invite_url=invite_url, reward=reward),
        "attachments": attachments,
    }
