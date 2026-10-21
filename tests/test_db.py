from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from server.gizmoapp_server.db import (
    LATEST_SCHEMA_VERSION,
    SCHEMA_MIGRATIONS,
    backup_database,
    database_readiness,
    initialize_database,
    verify_database_schema,
)


class DatabaseHardeningTests(unittest.TestCase):
    def test_avatar_color_migration_preserves_existing_accounts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "accounts.sqlite3"
            with sqlite3.connect(db_path) as connection:
                connection.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY)")
                for version in range(1, 10):
                    connection.executescript(SCHEMA_MIGRATIONS[version])
                    connection.execute("INSERT INTO schema_migrations VALUES (?)", (version,))
                connection.execute("INSERT INTO account_users VALUES (?,?,?,?,?)", ("old-user", "Existing name", "existing@example.com", "existing-password-hash", 100))
                connection.commit()
            initialize_database({"DB_PATH": db_path})
            initialize_database({"DB_PATH": db_path})
            with sqlite3.connect(db_path) as connection:
                self.assertEqual(connection.execute("SELECT name,email,password_hash,avatar_color FROM account_users").fetchone(), ("Existing name", "existing@example.com", "existing-password-hash", "#4667a5"))

    def test_session_revision_migration_preserves_existing_sessions(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "app.sqlite3"
            connection = sqlite3.connect(db_path)
            connection.execute(
                """
                CREATE TABLE schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            for version in range(1, 6):
                connection.executescript(
                    "BEGIN IMMEDIATE;\n"
                    f"{SCHEMA_MIGRATIONS[version]}\n"
                    f"INSERT INTO schema_migrations (version) VALUES ({version});\n"
                    "COMMIT;"
                )
            connection.execute(
                """
                INSERT INTO debate_sessions (
                    id, question, participants_json, messages_json, draft,
                    debate_started, paused, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("a" * 32, "Existing question", "[]", "[]", "", 0, 0, "created", "updated"),
            )
            connection.commit()
            connection.close()

            initialize_database({"DB_PATH": db_path})

            connection = sqlite3.connect(db_path)
            try:
                session = connection.execute(
                    "SELECT question, revision, pending_user_messages_json FROM debate_sessions WHERE id = ?",
                    ("a" * 32,),
                ).fetchone()
            finally:
                connection.close()
            self.assertEqual(session, ("Existing question", 1, "[]"))

    def test_database_uses_efs_safe_rollback_journal_and_current_schema(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "app.sqlite3"
            config = {"DB_PATH": db_path}
            initialize_database(config)

            connection = sqlite3.connect(db_path)
            try:
                journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
                version = connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
            finally:
                connection.close()

            self.assertEqual(journal_mode.lower(), "delete")
            self.assertEqual(version, LATEST_SCHEMA_VERSION)
            verify_database_schema(config)
            self.assertTrue(database_readiness(config)[0])

    def test_database_honors_an_explicit_local_wal_profile(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "app.sqlite3"
            config = {
                "DB_PATH": db_path,
                "SQLITE_JOURNAL_MODE": "WAL",
                "SQLITE_SYNCHRONOUS": "NORMAL",
            }
            initialize_database(config)
            connection = sqlite3.connect(db_path)
            try:
                self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0].lower(), "wal")
            finally:
                connection.close()

    def test_backup_is_a_consistent_readable_database(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = {"DB_PATH": root / "app.sqlite3"}
            initialize_database(config)
            backup_path = backup_database(config, root / "backups" / "app.sqlite3")

            connection = sqlite3.connect(backup_path)
            try:
                version = connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
            finally:
                connection.close()
            self.assertEqual(version, LATEST_SCHEMA_VERSION)

    def test_outdated_database_is_not_ready(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config = {"DB_PATH": Path(temp_dir) / "empty.sqlite3"}
            sqlite3.connect(config["DB_PATH"]).close()

            ready, detail = database_readiness(config)
            self.assertFalse(ready)
            self.assertEqual(detail["database"], "schema-outdated")
            with self.assertRaisesRegex(RuntimeError, "schema is version 0"):
                verify_database_schema(config)


if __name__ == "__main__":
    unittest.main()
