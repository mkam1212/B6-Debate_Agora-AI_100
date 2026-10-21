# Debate Agora

Flask and SQLite serve a build-free JavaScript frontend. Product code lives in
`server/gizmoapp_server`; see `docs/agent-map.md` for entry points.

Preserve the established blue palette, quiet glass surfaces, pill controls and
concise product copy. Use the existing icon system and local assets.

Keep account boundaries, read-only sharing, transactional reply allowances and
durable streamed replies intact. Never use real users' credits or send real
emails for tests. Tests use temporary databases and mocked providers.

Run `make validate` with the project Python environment and Node.js available.
Run targeted tests during implementation and the full validation before release.
Python requirements are in `server/requirements.txt`; Node is for validation only.

Never commit `.env`, credentials, databases, runtime logs, or design preview data.
Use `.env.example` for safe configuration examples. Production startup uses
`deploy/gunicorn.conf.py`; migrate and back up the database before deploying.
