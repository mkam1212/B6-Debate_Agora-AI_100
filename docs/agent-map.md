# Code map

- `server/gizmoapp_server/__init__.py`: application setup and route registration.
- `config.py`, `db.py`: environment settings, schema migrations and backups.
- `account_api.py`, `account_usage.py`, `account_invites.py`: authentication,
  allowance accounting, reviews and invitation delivery.
- `debate_api.py`, `debate_chat.py`, `debate_rounds.py`: streamed conversations,
  speaker coordination, bounded rounds and summaries.
- `debate_replies.py`: atomically save completed replies and usage; preserve
  paid replies when rebasing draft saves.
- `session_access.py`, `attachments.py`, `agent_tools.py`: private histories,
  revocable viewing links, file ownership, retention and real web citations.
- `static/app/text/debate.js`: setup, composer, streaming and conversation state.
- `static/app/text/workspace.js`, `account.js`, `attachments.js`: sidebar,
  dialogs, account interactions and file controls.
- `templates/emails/`: shared verification and invitation email layouts.
- `tests/`: temporary databases and mocked network transports; frontend
  behavioral checks use Node's test runner.
- `docs/product-validation.md`: completed checks and remaining real-user trials.
- `deploy/gunicorn.conf.py`, `scripts/deploy_from_git.sh`: startup defaults and
  deployment validation, backup, rollback and readiness checks.

`var/` and untracked `design/` files are local artifacts, excluded from uploads.
The legacy discussion generation routes are retired; their saved records remain
readable. Historical external-tool fixtures are optional and skipped if absent.
