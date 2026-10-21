import base64
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path

from server.gizmoapp_server import create_app
from server.gizmoapp_server.account_email_design import invitation_email_content, verification_email_content


class EmailDocument(HTMLParser):
    def __init__(self, source):
        super().__init__()
        self.images, self.links, self.text, self.backgrounds = [], [], [], []
        self.feed(source)

    def handle_starttag(self, tag, attributes):
        attributes = dict(attributes)
        if tag == "img":
            self.images.append(attributes)
        if tag == "a":
            self.links.append(attributes.get("href"))
        if attributes.get("background"):
            self.backgrounds.append(attributes["background"])

    def handle_data(self, value):
        self.text.append(value)


class AccountEmailDesignTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        app = create_app({"TESTING": True, "DB_PATH": Path(temporary.name) / "email.sqlite3", "AUTO_MIGRATE": True,
                          "URL_PREFIX": "/agora", "PUBLIC_APP_URL": "https://agora.example.com"}, shell_variant="text")
        context = app.app_context()
        context.push()
        self.addCleanup(context.pop)

    def test_verification_variants_keep_code_copyable_and_purpose_clear(self):
        variants = {"register": "Verify your email.", "email": "Confirm your new email.", "reset": "Reset your password."}
        for purpose, heading in variants.items():
            with self.subTest(purpose=purpose):
                message = verification_email_content("012345", purpose)
                parsed = EmailDocument(message["html"])
                self.assertIn("012345", parsed.text)
                self.assertIn(heading, parsed.text)
                self.assertIn("Expires in 10 minutes", parsed.text)
                self.assertIn("012345", message["text"])
                self.assertLess(len(message["html"].encode()), 50_000)
                self.assertEqual(parsed.images[0]["src"], "cid:agora-wordmark")
                attachment = message["attachments"][0]
                self.assertEqual(attachment["content_id"], "agora-wordmark")
                self.assertTrue(base64.b64decode(attachment["content"]).startswith(b"\x89PNG\r\n\x1a\n"))
                self.assertEqual(parsed.backgrounds, ["cid:agora-banner"])
                banner = message["attachments"][1]
                self.assertEqual(banner["content_id"], "agora-banner")
                self.assertTrue(base64.b64decode(banner["content"]).startswith(b"\xff\xd8"))
                self.assertIn("https://agora.example.com/agora/", parsed.links)

    def test_invitation_preserves_referral_url_dynamic_reward_and_plain_text(self):
        url = "https://agora.example.com/agora/?invite=abc_123&campaign=friend"
        message = invitation_email_content("Guolinn", url, 45)
        parsed = EmailDocument(message["html"])
        self.assertEqual(parsed.links.count(url), 2)
        self.assertIn("45 extra AI replies.", parsed.text)
        self.assertIn("Guolinn", parsed.text)
        self.assertIn("Join Debate Agora", parsed.text)
        self.assertIn(url, message["text"])
        self.assertIn("45 extra AI replies", message["text"])
        self.assertIn("verify your email", message["text"])
        self.assertNotIn('src="http', message["html"])

    def test_inviter_names_are_escaped_in_both_visible_and_preheader_content(self):
        name = '<img src=x onerror="alert(1)"> & friends'
        message = invitation_email_content(name, "https://agora.example.com/?invite=safe", 30)
        self.assertNotIn(name, message["html"])
        self.assertIn("&lt;img", message["html"])
        self.assertEqual(len(EmailDocument(message["html"]).images), 1)


if __name__ == "__main__":
    unittest.main()
