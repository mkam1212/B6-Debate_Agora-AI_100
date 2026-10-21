# Production deployment

## Live URLs

| Destination | URL |
| --- | --- |
| Website | https://debate-agora.beringtech.com/ |
| Workspace | https://debate-agora.beringtech.com/app |
| Readiness check | https://debate-agora.beringtech.com/readyz |

The application is hosted on AWS Lightsail. Cloudflare proxies the public domain to Nginx over HTTPS; Nginx forwards application requests to Gunicorn on the server's loopback interface. Flask serves the frontend and API, and SQLite stores accounts, conversations, attachments, and reply allowances.

GitHub contains the application source. A push to `main` does not automatically update the live service. Local development and production use separate databases and accounts.

## Configuration

Start with [`.env.example`](../.env.example). Keep real credentials in the server's protected environment, outside Git.

| Setting | Production value or purpose |
| --- | --- |
| `GIZMOAPP_SHELL` | `text` |
| `GIZMOAPP_ENV` | `production`; enables secure session cookies |
| `GIZMOAPP_PUBLIC_URL` | `https://debate-agora.beringtech.com` for this deployment |
| `GIZMOAPP_URL_PREFIX` | Empty for this deployment; `/app` is the workspace route |
| `GIZMOAPP_SECRET_KEY` | A unique, persistent secret |
| `GIZMOAPP_DB_PATH` | An absolute path in persistent, writable storage |
| `GIZMOAPP_AUTO_MIGRATE` | `0`; run migrations explicitly before starting |
| `GIZMOAPP_TRUST_PROXY` | `1` only behind a trusted proxy that overwrites forwarded headers |
| `GIZMOAPP_SQLITE_JOURNAL_MODE` | `WAL` |
| `GIZMOAPP_SQLITE_SYNCHRONOUS` | `FULL` |
| `GIZMO_LLM_API_KEY`, `GIZMO_LLM_BASE_URL`, `GIZMO_LLM_MODEL` | Server-side model connection |
| `RESEND_API_KEY`, `RESEND_FROM_EMAIL` | Email provider credentials and verified sender |
| `GIZMO_ACCOUNT_STARTER_REPLIES` | `50` |
| `GIZMO_ACCOUNT_INVITE_REWARD` | `100` for each eligible account |

For another installation, replace the public URL with that installation's own HTTPS origin. It must be reachable by email recipients.

## Service and proxy

The checked-in [Gunicorn configuration](../deploy/gunicorn.conf.py) defaults to one worker, eight threads, and `127.0.0.1:8001`. Run `server.wsgi:app` from the repository root under a service manager and a dedicated service user.

Nginx must disable response buffering for streamed replies and allow enough time for a reply to finish. Keep the default request-body limit at 1 MiB and allow 21 MiB on `/api/debate/attachments` for files and multipart overhead. Overwrite forwarded headers, restrict trusted client-IP headers to the proxy's published address ranges, and reject unknown hostnames.

The live deployment uses Cloudflare Full (strict) for this hostname, a valid origin TLS certificate with automatic renewal, and an HTTP-to-HTTPS redirect. Public web ports are 80 and 443; SSH is restricted to the operator and AWS browser access.

## Updates and backups

1. Run `make validate` before releasing application changes. Tests use temporary databases and mocked providers.
2. Load the same protected environment as the running service. Create a consistent backup with `.venv/bin/python server/manage.py backup-db --shell text` and retain it outside Git.
3. Stop the application service before changing the running checkout or schema. Install any changed Python requirements, then run `.venv/bin/python server/manage.py init-db --shell text` to apply migrations.
4. Run `.venv/bin/python server/manage.py check-ready --shell text`, restart the application service, and check the public website, workspace, and readiness URL.

Production creates a daily SQLite backup and retains the latest 14 copies on the server. These local backups do not protect against losing the whole instance; maintain a separate protected copy before destructive maintenance. Never commit database files, backups, logs, private keys, or real environment files.
