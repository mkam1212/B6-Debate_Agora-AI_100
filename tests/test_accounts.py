import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from werkzeug.security import check_password_hash

from server.gizmoapp_server import create_app
from server.gizmoapp_server.account_api import send_verification_email
from server.gizmoapp_server.account_usage import reserve_reply, finish_reply
from server.gizmoapp_server import llm


class AccountTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db_path = Path(self.temp.name) / "accounts.sqlite3"
        self.app = create_app({
            "TESTING": True, "DB_PATH": self.db_path, "SECRET_KEY": "account-test-secret",
            "URL_PREFIX": "/agora", "AUTO_MIGRATE": True,
            "RESEND_API_KEY": "test-key-not-a-real-secret", "RESEND_FROM_EMAIL": "Debate Agora <accounts@example.com>",
            "PUBLIC_APP_URL": "https://agora.example.com",
        }, shell_variant="text")
        self.client = self.app.test_client()
        self.mail = patch("server.gizmoapp_server.account_api.send_verification_email").start()
        self.addCleanup(patch.stopall)
        self.csrf = self.client.get("/agora/api/account/session").json["csrfToken"]

    def call(self, path, data=None, method="POST", client=None, csrf=None):
        return (client or self.client).open(f"/agora/api/account/{path}", method=method, json=data or {}, headers={"X-CSRF-Token": csrf or self.csrf})

    def register(self, email="person@example.com", avatar_color=None):
        data = {"purpose": "register", "email": email, "name": "Guolinn", "password": "test-password-123"}
        if avatar_color is not None:
            data["avatarColor"] = avatar_color
        response = self.call("verification", data)
        self.assertEqual(response.status_code, 200)
        code = self.mail.call_args.args[1]
        challenge_id = response.json["challengeId"]
        response = self.call("verify", {"challengeId": challenge_id, "code": code})
        self.assertEqual(response.status_code, 200, response.json)
        self.csrf = response.json["csrfToken"]
        return response, challenge_id, code

    def sql(self, query, args=()):
        with sqlite3.connect(self.db_path) as connection:
            return connection.execute(query, args).fetchall()

    def test_registration_requires_code_stores_hash_and_prevents_replay(self):
        response, challenge_id, code = self.register()
        self.assertEqual(response.json["user"]["email"], "person@example.com")
        password_hash = self.sql("SELECT password_hash FROM account_users")[0][0]
        self.assertNotIn("test-password-123", password_hash)
        self.assertTrue(check_password_hash(password_hash, "test-password-123"))
        self.assertEqual(self.call("verify", {"challengeId": challenge_id, "code": code}).status_code, 400)
        self.assertFalse(self.sql("SELECT * FROM account_challenges"))
        session_response = self.client.get("/agora/api/account/session")
        self.assertEqual(session_response.json["starterAllowance"], 50)
        self.assertEqual(session_response.json["usage"]["remaining"], 50)
        self.assertEqual(session_response.json["usage"]["rewardPerInvite"], 100)
        self.assertEqual(self.sql("SELECT delta FROM account_usage_entries WHERE kind='welcome'"), [(50,)])
        self.assertEqual(session_response.headers["Cache-Control"], "no-store")
        self.assertNotIn(password_hash, session_response.text)

    def test_csrf_rejects_missing_and_stale_tokens(self):
        self.assertEqual(self.client.post("/agora/api/account/sign-out", json={}).status_code, 403)
        old = self.csrf
        self.register()
        self.assertEqual(self.call("profile", {"name": "New"}, "PATCH", csrf=old).status_code, 403)

    def test_malformed_input_is_rejected_without_server_errors(self):
        for purpose in ([], {}, None):
            self.assertEqual(self.call("verification", {"purpose": purpose}).status_code, 400)
        response = self.client.post("/agora/api/account/sign-out", json={}, headers={"X-CSRF-Token": "é"})
        self.assertEqual(response.status_code, 403)

    def test_account_cookie_is_http_only_and_scoped_to_application(self):
        self.app.config["SESSION_COOKIE_SECURE"] = True
        response = self.app.test_client().get("/agora/api/account/session")
        cookie = response.headers["Set-Cookie"]
        for flag in ("HttpOnly", "SameSite=Lax", "Secure", "Path=/agora"):
            self.assertIn(flag, cookie)

    def test_logout_revokes_copied_session_cookie(self):
        self.register()
        cookie = self.client.get_cookie("session", path="/agora").value
        self.assertEqual(self.call("sign-out").status_code, 200)
        self.client.set_cookie("session", cookie, path="/agora")
        self.assertIsNone(self.client.get("/agora/api/account/session").json["user"])

    def test_sign_in_and_name_change(self):
        self.register()
        result = self.call("sign-out")
        self.csrf = result.json["csrfToken"]
        self.assertEqual(self.call("sign-in", {"email": "person@example.com", "password": "wrong"}).status_code, 401)
        result = self.call("sign-in", {"email": "PERSON@example.com", "password": "test-password-123"})
        self.csrf = result.json["csrfToken"]
        result = self.call("profile", {"name": "Updated name"}, "PATCH")
        self.assertEqual(result.json["user"]["name"], "Updated name")

    def test_avatar_color_survives_verification_login_and_profile_edits(self):
        response, _, _ = self.register(avatar_color="#8260A4")
        self.assertEqual(response.json["user"]["avatarColor"], "#8260a4")
        self.assertEqual(self.sql("SELECT avatar_color FROM account_users")[0][0], "#8260a4")
        self.csrf = self.call("sign-out").json["csrfToken"]
        response = self.call("sign-in", {"email": "person@example.com", "password": "test-password-123"})
        self.csrf = response.json["csrfToken"]
        self.assertEqual(response.json["user"]["avatarColor"], "#8260a4")
        response = self.call("profile", {"name": "New name"}, "PATCH")
        self.assertEqual(response.json["user"]["avatarColor"], "#8260a4")
        response = self.call("profile", {"name": "New name", "avatarColor": "#347b73"}, "PATCH")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get("/agora/api/account/session").json["user"]["avatarColor"], "#347b73")

    def test_registration_rejects_invalid_avatar_styles_without_sending_email(self):
        for color in ("red", "#fff;display:none", None):
            response = self.call("verification", {"purpose": "register", "email": "person@example.com", "name": "Name", "password": "test-password-123", "avatarColor": color})
            self.assertEqual(response.status_code, 400, response.json)
        self.mail.assert_not_called()
        self.assertFalse(self.sql("SELECT * FROM account_users"))

    def test_invalid_profile_color_does_not_change_account(self):
        self.register()
        response = self.call("profile", {"name": "Unwanted change", "avatarColor": "url(https://example.com)"}, "PATCH")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.get("/agora/api/account/session").json["user"]["name"], "Guolinn")

    def test_email_changes_only_after_code_and_requires_same_account(self):
        self.register()
        response = self.call("verification", {"purpose": "email", "email": "new@example.com", "currentPassword": "test-password-123"})
        challenge_id, code = response.json["challengeId"], self.mail.call_args.args[1]
        self.assertEqual(self.sql("SELECT email FROM account_users")[0][0], "person@example.com")
        stranger = self.app.test_client()
        stranger_csrf = stranger.get("/agora/api/account/session").json["csrfToken"]
        self.assertEqual(self.call("verify", {"challengeId": challenge_id, "code": code}, client=stranger, csrf=stranger_csrf).status_code, 401)
        result = self.call("verify", {"challengeId": challenge_id, "code": code})
        self.assertEqual(result.status_code, 200, result.json)
        self.assertEqual(result.json["user"]["email"], "new@example.com")

    def test_password_change_revokes_other_sessions_and_checks_current_password(self):
        self.register()
        second = self.app.test_client()
        second_csrf = second.get("/agora/api/account/session").json["csrfToken"]
        self.call("sign-in", {"email": "person@example.com", "password": "test-password-123"}, client=second, csrf=second_csrf)
        self.assertEqual(self.call("password", {"currentPassword": "wrong", "newPassword": "updated-password-123"}, "PATCH").status_code, 401)
        response = self.call("password", {"currentPassword": "test-password-123", "newPassword": "updated-password-123"}, "PATCH")
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(second.get("/agora/api/account/session").json["user"])
        self.assertTrue(check_password_hash(self.sql("SELECT password_hash FROM account_users")[0][0], "updated-password-123"))

    def test_codes_expire_and_lock_after_five_wrong_attempts(self):
        response = self.call("verification", {"purpose": "register", "email": "p@example.com", "name": "Name", "password": "test-password-123"})
        challenge_id, code = response.json["challengeId"], self.mail.call_args.args[1]
        wrong = "111111" if code != "111111" else "222222"
        for _ in range(5):
            self.assertEqual(self.call("verify", {"challengeId": challenge_id, "code": wrong}).status_code, 400)
        self.assertEqual(self.call("verify", {"challengeId": challenge_id, "code": code}).status_code, 400)
        with sqlite3.connect(self.db_path) as connection:
            connection.execute("UPDATE account_challenges SET expires_at=0, attempts=0")
        self.assertEqual(self.call("verify", {"challengeId": challenge_id, "code": code}).status_code, 400)
        self.assertFalse(self.sql("SELECT * FROM account_users"))

    def test_verification_email_rate_limit_and_no_code_in_response(self):
        for _ in range(6):
            response = self.call("verification", {"purpose": "reset", "email": "unknown@example.com"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(set(response.json), {"challengeId", "expiresIn"})
        self.assertEqual(self.call("verification", {"purpose": "reset", "email": "unknown@example.com"}).status_code, 429)
        self.mail.assert_not_called()

    def test_password_reset_with_email_code(self):
        self.register()
        self.csrf = self.call("sign-out").json["csrfToken"]
        response = self.call("verification", {"purpose": "reset", "email": "person@example.com"})
        response = self.call("verify", {"challengeId": response.json["challengeId"], "code": self.mail.call_args.args[1], "newPassword": "recovered-password-123"})
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json["user"]["email"], "person@example.com")

    def test_resend_request_uses_server_settings(self):
        response = Mock(status=200)
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        with self.app.app_context(), patch("server.gizmoapp_server.account_api.urlopen", return_value=response) as send:
            send_verification_email("person@example.com", "123456", "register", "test-challenge")
        request = send.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual(request.full_url, "https://api.resend.com/emails")
        self.assertEqual(payload["from"], "Debate Agora <accounts@example.com>")
        self.assertEqual(payload["to"], ["person@example.com"])
        self.assertIn("123456", payload["text"])
        self.assertIn("Verify your email.", payload["html"])
        self.assertEqual(payload["attachments"][0]["content_id"], "agora-wordmark")

    def test_failed_delivery_discards_code_without_disclosing_provider_error(self):
        from server.gizmoapp_server.account_api import AccountError
        self.mail.side_effect = AccountError("Email unavailable.", 502)
        response = self.call("verification", {"purpose": "register", "email": "person@example.com", "name": "Name", "password": "test-password-123"})
        self.assertEqual(response.status_code, 502)
        self.assertFalse(self.sql("SELECT * FROM account_challenges"))

    def test_guests_cannot_start_or_request_ai(self):
        with patch("server.gizmoapp_server.debate_api.generate_participant_roles") as model, patch("server.gizmoapp_server.debate_api.generate_participant_role") as one_model:
            for endpoint in ("roles/generate", "roles/generate-one", "sessions", "sessions/" + "a" * 32 + "/reply", "sessions/" + "a" * 32 + "/summary", "discussions/example/run"):
                response = self.client.post("/agora/api/debate/" + endpoint, json={})
                self.assertEqual(response.status_code, 401, endpoint)
            model.assert_not_called()
            one_model.assert_not_called()
        self.assertEqual(self.client.get("/agora/api/account/usage").status_code, 401)

    def test_invitation_rewards_both_only_after_verified_new_registration(self):
        self.register()
        usage = self.client.get("/agora/api/account/usage").json["usage"]
        self.assertEqual(usage["remaining"], 50)
        friend = self.app.test_client()
        friend.get("/agora/?invite=" + usage["inviteCode"])
        session = friend.get("/agora/api/account/session").json
        self.assertEqual(session["invitation"]["reward"], 100)
        csrf = session["csrfToken"]
        response = self.call("verification", {"purpose": "register", "name": "Friend", "email": "friend@example.com", "password": "friend-password-123"}, client=friend, csrf=csrf)
        challenge_id, code = response.json["challengeId"], self.mail.call_args.args[1]
        self.assertEqual(self.client.get("/agora/api/account/usage").json["usage"]["remaining"], 50)
        self.assertEqual(self.client.get("/agora/api/account/invites").json["invites"], [])
        verified = self.call("verify", {"challengeId": challenge_id, "code": code}, client=friend, csrf=csrf)
        self.assertEqual(verified.status_code, 200, verified.json)
        for client in (self.client, friend):
            result = client.get("/agora/api/account/usage").json["usage"]
            self.assertEqual(result["remaining"], 150)
            self.assertEqual(result["earned"], 100)
        self.assertEqual(self.client.get("/agora/api/account/usage").json["usage"]["invites"], 1)
        history = self.client.get("/agora/api/account/invites").json["invites"]
        self.assertEqual([(row["name"], row["reward"]) for row in history], [("Friend", 100)])
        self.assertEqual(friend.get("/agora/api/account/invites").json["invites"], [])
        replay = self.call("verify", {"challengeId": challenge_id, "code": code}, client=friend, csrf=verified.json["csrfToken"])
        self.assertEqual(replay.status_code, 400)
        self.assertEqual(len(self.sql("SELECT * FROM account_referrals")), 1)
        self.assertEqual(self.client.get("/agora/api/account/usage").json["usage"]["remaining"], 150)

    def test_existing_account_and_own_invite_do_not_earn_rewards(self):
        self.register()
        usage = self.client.get("/agora/api/account/usage").json["usage"]
        self.client.get("/agora/?invite=" + usage["inviteCode"])
        self.csrf = self.call("sign-out").json["csrfToken"]
        response = self.call("sign-in", {"email": "person@example.com", "password": "test-password-123"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get("/agora/api/account/usage").json["usage"]["remaining"], 50)
        self.assertFalse(self.sql("SELECT * FROM account_referrals"))

    def test_invite_history_is_private_verified_filtered_and_paginated(self):
        self.assertEqual(self.client.get("/agora/api/account/invites").status_code, 401)
        self.register()
        inviter_id = self.sql("SELECT id FROM account_users")[0][0]
        import time
        now = int(time.time())
        with sqlite3.connect(self.db_path) as connection:
            for i in range(54):
                user_id = f"friend-{i:03}"
                connection.execute("INSERT INTO account_users (id,name,email,password_hash,created_at,avatar_color) VALUES (?,?,?,?,?,?)", (user_id, f"Friend {i}", f"friend{i}@example.com", "unused-test-hash", now, "#2563eb"))
                # Two older referrals exercise the date filter; one belongs to another inviter.
                joined = now - (100 if i == 52 else 40 if i == 51 else 1) * 86400
                connection.execute("INSERT INTO account_referrals VALUES (?,?,?,?)", (user_id, "friend-000" if i == 53 else inviter_id, 30, joined))
        response = self.client.get("/agora/api/account/invites")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        history = response.json
        self.assertEqual(len(history["invites"]), 50)
        self.assertTrue(history["hasMore"])
        self.assertEqual(history["nextOffset"], 50)
        self.assertEqual(set(history["invites"][0]), {"name", "avatarColor", "reward", "joinedAt"})
        self.assertNotIn("Friend 53", [row["name"] for row in history["invites"]])
        more = self.client.get("/agora/api/account/invites?offset=50").json
        self.assertEqual([row["name"] for row in more["invites"]], ["Friend 50", "Friend 51"])
        self.assertFalse(more["hasMore"])
        recent = self.client.get("/agora/api/account/invites?period=30&offset=50").json
        self.assertEqual([row["name"] for row in recent["invites"]], ["Friend 50"])
        older = self.client.get("/agora/api/account/invites?period=all&offset=50").json
        self.assertEqual([row["name"] for row in older["invites"]], ["Friend 50", "Friend 51", "Friend 52"])
        for query in ("period=365", "offset=-1", "offset=oops"):
            self.assertEqual(self.client.get("/agora/api/account/invites?" + query).status_code, 400)

    def test_changing_starter_policy_keeps_previously_granted_replies(self):
        self.app.config["ACCOUNT_STARTER_REPLIES"] = 50
        self.register()
        self.app.config["ACCOUNT_STARTER_REPLIES"] = 10
        result = self.client.get("/agora/api/account/session").json
        self.assertEqual(result["starterAllowance"], 10)
        self.assertEqual(result["usage"]["remaining"], 50)
        self.assertEqual(result["usage"]["rewardPerInvite"], 100)

    def mail_response(self):
        response = Mock(status=200)
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        return response

    def test_email_invites_send_separate_private_messages_and_deduplicate(self):
        self.assertEqual(self.call("invites", {"emails": "a@example.com"}).status_code, 401)
        self.register()
        with patch("server.gizmoapp_server.account_invites.urlopen", return_value=self.mail_response()) as send:
            response = self.call("invites", {"emails": " A@example.com, b@example.com, a@example.com"})
            self.assertEqual(response.status_code, 200, response.json)
            self.assertEqual(response.json, {"sent": ["a@example.com", "b@example.com"], "alreadyInvited": [], "alreadyRegistered": [], "failed": []})
            self.assertEqual(send.call_count, 2)
            for call in send.call_args_list:
                outgoing = call.args[0]
                body = json.loads(outgoing.data)
                self.assertEqual(outgoing.full_url, "https://api.resend.com/emails")
                self.assertEqual(len(body["to"]), 1)
                self.assertEqual(body["from"], "Debate Agora <accounts@example.com>")
                self.assertIn("https://agora.example.com/agora/?invite=", body["text"])
                self.assertIn("You’re invited.", body["html"])
                self.assertIn("100 extra AI replies", body["text"])
                self.assertEqual(body["attachments"][0]["content_id"], "agora-wordmark")
                self.assertNotIn("127.0.0.1", body["text"])
                self.assertTrue(outgoing.get_header("Idempotency-key").startswith("invite-"))
            duplicate = self.call("invites", {"emails": "a@example.com"})
            self.assertEqual(duplicate.json["alreadyInvited"], ["a@example.com"])
            self.assertEqual(send.call_count, 2)
        usage = self.client.get("/agora/api/account/usage").json["usage"]
        self.assertEqual((usage["remaining"], usage["earned"]), (50, 0))
        history = self.client.get("/agora/api/account/invites").json["invites"]
        self.assertEqual({row["email"] for row in history}, {"a@example.com", "b@example.com"})
        self.assertTrue(all(row["status"] == "pending" and row["reward"] == 0 for row in history))
        stranger = self.app.test_client()
        self.assertEqual(stranger.get("/agora/api/account/invites").status_code, 401)

    def test_email_invite_validation_csrf_and_local_links_never_send(self):
        self.register()
        with patch("server.gizmoapp_server.account_invites.urlopen") as send:
            self.assertEqual(self.client.post("/agora/api/account/invites", json={"emails": "a@example.com"}).status_code, 403)
            for emails in ("not-email", "", ["a@example.com"] * 6, "person@example.com", [None]):
                self.assertEqual(self.call("invites", {"emails": emails}).status_code, 400)
            for origin in ("", "http://127.0.0.1:8001", "http://10.0.0.2", "https://user:pass@example.com", "https://example.com/path"):
                self.app.config["PUBLIC_APP_URL"] = origin
                self.assertEqual(self.call("invites", {"emails": "a@example.com"}).status_code, 503)
            send.assert_not_called()
        self.assertFalse(self.sql("SELECT * FROM account_email_invites"))

    def test_email_invites_skip_registered_addresses_in_mixed_batch(self):
        self.register()
        from tests.auth_helpers import authenticated_client
        from server.gizmoapp_server.db import get_db
        other = authenticated_client(self.app)
        uid = other.get('/agora/api/account/session').json['user']['id']
        with self.app.app_context():
            get_db().execute('UPDATE account_users SET email=? WHERE id=?', ('Registered@Example.com', uid))
            get_db().commit()
        before = self.client.get('/agora/api/account/usage').json['usage']['remaining']
        with patch('server.gizmoapp_server.account_invites.urlopen', return_value=self.mail_response()) as send:
            result = self.call('invites', {'emails': ' REGISTERED@example.com, new@example.com'}).json
        self.assertEqual(result['alreadyRegistered'], ['registered@example.com'])
        self.assertEqual(result['sent'], ['new@example.com'])
        self.assertEqual(send.call_count, 1)
        self.assertEqual(json.loads(send.call_args.args[0].data)['to'], ['new@example.com'])
        self.assertEqual(self.sql('SELECT email FROM account_email_invites'), [('new@example.com',)])
        self.assertEqual(self.client.get('/agora/api/account/usage').json['usage']['remaining'], before)

    def test_retry_does_not_email_a_recipient_who_registered_after_failed_delivery(self):
        self.register()
        from urllib.error import URLError
        with patch('server.gizmoapp_server.account_invites.urlopen', side_effect=URLError('offline')):
            self.call('invites', {'emails': 'registered@example.com'})
        from tests.auth_helpers import authenticated_client
        from server.gizmoapp_server.db import get_db
        other = authenticated_client(self.app)
        uid = other.get('/agora/api/account/session').json['user']['id']
        with self.app.app_context():
            get_db().execute('UPDATE account_users SET email=? WHERE id=?', ('registered@example.com', uid))
            get_db().commit()
        with patch('server.gizmoapp_server.account_invites.urlopen') as send:
            response = self.call('invites', {'emails': 'registered@example.com'})
            send.assert_not_called()
        self.assertEqual(response.json['alreadyRegistered'], ['registered@example.com'])
        self.assertEqual(response.json['sent'], [])

    def test_failed_email_invite_retries_with_same_payload_and_key(self):
        from urllib.error import URLError
        self.register()
        with patch("server.gizmoapp_server.account_invites.urlopen", side_effect=URLError("sensitive-provider-response")) as send:
            response = self.call("invites", {"emails": "a@example.com"})
            first = send.call_args.args[0]
        self.assertEqual(response.json["failed"], ["a@example.com"])
        self.assertNotIn("sensitive-provider-response", response.text)
        self.assertEqual(self.sql("SELECT state FROM account_email_invites"), [("failed",)])
        with patch("server.gizmoapp_server.account_invites.urlopen", return_value=self.mail_response()) as send:
            response = self.call("invites", {"emails": "a@example.com"})
            second = send.call_args.args[0]
        self.assertEqual(response.json["sent"], ["a@example.com"])
        self.assertEqual(first.get_header("Idempotency-key"), second.get_header("Idempotency-key"))
        self.assertEqual(first.data, second.data)

    def test_email_invites_report_partial_failure_without_resending_successes(self):
        from urllib.error import URLError
        self.register()
        def deliver(mail, **kwargs):
            if json.loads(mail.data)["to"] == ["b@example.com"]:
                raise URLError("unavailable")
            return self.mail_response()
        with patch("server.gizmoapp_server.account_invites.urlopen", side_effect=deliver):
            result = self.call("invites", {"emails": "a@example.com,b@example.com"}).json
        self.assertEqual(result["sent"], ["a@example.com"])
        self.assertEqual(result["failed"], ["b@example.com"])
        with patch("server.gizmoapp_server.account_invites.urlopen", return_value=self.mail_response()) as send:
            result = self.call("invites", {"emails": "a@example.com,b@example.com"}).json
            self.assertEqual(send.call_count, 1)
            self.assertEqual(result["alreadyInvited"], ["a@example.com"])
            self.assertEqual(result["sent"], ["b@example.com"])

    def test_email_invitation_becomes_accepted_once_after_verified_registration(self):
        self.register()
        with patch("server.gizmoapp_server.account_invites.urlopen", return_value=self.mail_response()):
            self.call("invites", {"emails": "friend@example.com"})
        usage = self.client.get("/agora/api/account/usage").json["usage"]
        friend = self.app.test_client()
        friend.get("/agora/?invite=" + usage["inviteCode"])
        csrf = friend.get("/agora/api/account/session").json["csrfToken"]
        result = self.call("verification", {"purpose": "register", "name": "Friend", "email": "friend@example.com", "password": "friend-password-123"}, client=friend, csrf=csrf)
        code = self.mail.call_args.args[1]
        before = self.client.get("/agora/api/account/invites").json["invites"]
        self.assertEqual([(row["status"], row["reward"]) for row in before], [("pending", 0)])
        response = self.call("verify", {"challengeId": result.json["challengeId"], "code": code}, client=friend, csrf=csrf)
        self.assertEqual(response.status_code, 200)
        history = self.client.get("/agora/api/account/invites").json["invites"]
        self.assertEqual([(row["name"], row["email"], row["status"], row["reward"]) for row in history], [("Friend", "friend@example.com", "accepted", 100)])
        self.assertEqual(self.client.get("/agora/api/account/usage").json["usage"]["remaining"], 150)
        self.assertEqual(friend.get("/agora/api/account/usage").json["usage"]["remaining"], 150)
        self.assertEqual(friend.get("/agora/api/account/invites").json["invites"], [])

    def test_invitation_mail_is_rate_limited_and_requires_provider_settings(self):
        self.register()
        self.app.config["RESEND_API_KEY"] = ""
        self.assertEqual(self.call("invites", {"emails": "a@example.com"}).status_code, 503)
        self.app.config["RESEND_API_KEY"] = "test-key"
        with patch("server.gizmoapp_server.account_invites.urlopen", return_value=self.mail_response()) as send:
            for _ in range(11):
                self.assertEqual(self.call("invites", {"emails": "a@example.com"}).status_code, 200)
            self.assertEqual(self.call("invites", {"emails": "b@example.com"}).status_code, 429)
            self.assertEqual(send.call_count, 1)

    def reply_session(self):
        payload = {"question": "Should we launch?", "participants": [{"id": "maya", "name": "Maya", "avatar": "M", "roleType": "Analyst", "prompt": "Challenge assumptions."}], "messages": [], "draft": "", "debateStarted": True, "paused": True}
        response = self.client.post("/agora/api/debate/sessions", json=payload)
        self.assertEqual(response.status_code, 201, response.json)
        return "/agora/api/debate/sessions/" + response.json["session"]["id"] + "/reply"

    def test_successful_reply_spends_one_failed_reply_is_refunded(self):
        self.register()
        endpoint = self.reply_session()
        with patch("server.gizmoapp_server.debate_api.generate_role_reply", return_value=("Test a smaller launch first.", False, "plain_text_v1")):
            self.assertEqual(self.client.post(endpoint, json={"participantId": "maya"}).status_code, 200)
        with patch("server.gizmoapp_server.debate_api.generate_role_reply", side_effect=llm.CourseLLMError("Unavailable")):
            self.assertEqual(self.client.post(endpoint, json={"participantId": "maya"}).status_code, 503)
        usage = self.client.get("/agora/api/account/usage").json["usage"]
        self.assertEqual((usage["remaining"], usage["used"]), (49, 1))

    def test_streaming_success_and_disconnect_account_for_one_or_zero(self):
        self.register()
        endpoint = self.reply_session()
        events = [{"type": "mode", "replyMode": "plain_text_v1"}, {"type": "metadata", "waitingForUser": False}, {"type": "delta", "text": "Test a pilot first."}]
        with patch("server.gizmoapp_server.debate_api.stream_role_reply", return_value=iter(events)):
            response = self.client.post(endpoint, json={"participantId": "maya"}, headers={"Accept": "text/event-stream"})
            self.assertIn('"type": "done"', response.text)
        def broken_stream(*args, **kwargs):
            yield from events[:2]
            yield events[2]
            raise llm.CourseLLMError("Interrupted")
        with patch("server.gizmoapp_server.debate_api.stream_role_reply", side_effect=broken_stream):
            response = self.client.post(endpoint, json={"participantId": "maya"}, headers={"Accept": "text/event-stream"})
            self.assertIn('"type": "error"', response.text)
        with patch("server.gizmoapp_server.debate_api.stream_role_reply", return_value=iter(events)):
            response = self.client.post(endpoint, json={"participantId": "maya"}, headers={"Accept": "text/event-stream"}, buffered=False)
            next(iter(response.response))
            response.close()
        usage = self.client.get("/agora/api/account/usage").json["usage"]
        self.assertEqual((usage["remaining"], usage["used"]), (49, 1))

    def test_parallel_reservations_cannot_overspend_and_completion_is_idempotent(self):
        from concurrent.futures import ThreadPoolExecutor
        from server.gizmoapp_server.account_api import AccountError
        self.register()
        user_id = self.sql("SELECT id FROM account_users")[0][0]
        with sqlite3.connect(self.db_path) as connection:
            connection.execute("UPDATE account_wallets SET balance=1")
        def reserve():
            with self.app.app_context():
                try:
                    return reserve_reply(user_id)
                except AccountError as error:
                    return error.status
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: reserve(), range(2)))
        self.assertEqual(results.count(402), 1)
        reservation = next(result for result in results if isinstance(result, str))
        with self.app.app_context():
            finish_reply(reservation, True)
            finish_reply(reservation, True)
            finish_reply(reservation, False)
        usage = self.client.get("/agora/api/account/usage").json["usage"]
        self.assertEqual((usage["remaining"], usage["used"]), (0, 1))
        endpoint = self.reply_session()
        with patch("server.gizmoapp_server.debate_api.select_participant_for_turn") as coordinator:
            self.assertEqual(self.client.post(endpoint, json={}).status_code, 402)
            coordinator.assert_not_called()


if __name__ == "__main__":
    unittest.main()
