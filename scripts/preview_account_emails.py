"""Render the actual account email templates with safe sample data; never send mail."""
import argparse
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from server.gizmoapp_server import create_app
from server.gizmoapp_server.account_email_design import invitation_email_content, verification_email_content


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=REPO_ROOT / "design/emails")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="agora-email-preview-") as directory:
        app = create_app({"TESTING": True, "DB_PATH": Path(directory) / "preview.sqlite3",
                          "AUTO_MIGRATE": True, "RESEND_API_KEY": "", "URL_PREFIX": "",
                          "PUBLIC_APP_URL": "https://debate-agora.beringtech.com"}, shell_variant="text")
        with app.app_context():
            messages = {purpose: verification_email_content("583294", purpose) for purpose in ("register", "email", "reset")}
            messages["invite"] = invitation_email_content("Guolinn", "https://debate-agora.beringtech.com/?invite=design-preview", 100)
        for name, message in messages.items():
            preview = message["html"]
            for attachment in message["attachments"]:
                preview = preview.replace("cid:" + attachment["content_id"], "data:" + attachment["content_type"] + ";base64," + attachment["content"])
            (args.output / f"{name}.html").write_text(preview, encoding="utf-8")
            (args.output / f"{name}.txt").write_text(message["text"], encoding="utf-8")
            print(args.output / f"{name}.html")


if __name__ == "__main__":
    main()
