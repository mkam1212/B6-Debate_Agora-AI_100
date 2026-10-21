# Debate Agora

Debate Agora lets users ask questions and watch AI participants discuss them from different viewpoints, challenge each other's ideas, and suggest possible answers.

**[Visit the website](https://debate-agora.beringtech.com/)** · **[Open Debate Agora](https://debate-agora.beringtech.com/app)**

**Course:** AI 100 | **Team:** Guolin, Matthew, Oliver

The live application runs on AWS Lightsail behind Nginx and Cloudflare, with HTTPS, streamed replies, verified email accounts, and persistent conversations. New accounts start with 50 AI replies; an eligible invitation adds 100 replies to each person after the new user verifies their email. See the [production deployment notes](docs/deployment.md) for configuration and maintenance.

## The Problem

When people ask an AI about a question or project idea, a single response can leave out important perspectives. Without someone to question the answer, it's hard to spot missing assumptions, consider other options, or judge whether the advice fits your situation. Debate Agora gives users several AI participants with different roles who discuss the question, so problems and alternatives surface before time is spent on a plan.

## Who It's For

- **Students** developing a course project or exploring a topic
- **Individuals** working on their own projects who want feedback from different viewpoints
- **Small teams** comparing plans and trade-offs

No coding or AI knowledge is needed. Users should be able to use a basic web app, describe their question in writing, and decide which suggestions fit their situation.

## How It Works

1. **Ask a question.** Enter your question and, optionally, background, goals, or constraints (e.g. "IPO or exit, unhealthy child").
2. **Start immediately or customize first.** The app generates three relevant perspectives and starts the discussion. Choose Customize agents first to edit the group before starting.
3. **Watch the debate.** Each participant gives an opening response with reasons, then they question and respond to each other's ideas.
4. **Join in.** Ask follow-ups, add details, correct mistakes, or `@mention` a participant to reply.
5. **Keep the useful parts.** Summary captures what changed, the key trade-off, evidence and unknowns, and one possible next step. A first checkpoint is saved after three AI replies. Default three-agent rounds close after nine replies: each speaker answers an objection to their position, then the conversation pauses and opens its saved summary. A new user message or Resume starts a fresh bounded round; no winner or consensus is invented. Other group sizes use 6–12 replies per round.
6. **Pause / Resume.** Stop and restart the discussion at any time.

## Interface

The desktop workspace uses the selected Debate Agora wordmark and locally bundled Lucide icons. The question screen focuses on one input; the debate screen adds three compact cards for sharing, agents, and resources. Agent edits open in a compact dialog with explicit Save and Cancel actions. Copy appears beneath each message. Sharing uses a menu beside its control; attachment controls open file pickers. On smaller screens, the sidebar and detail cards open from the header controls.

Home and the workspace share colors and glass materials in `surface-tokens.css`. `app-surfaces.css` applies translucent panels, inset edges, soft shadows, and pill controls across account dialogs and all three workspace steps. The workspace keeps its plain warm-white background; the home image stays on Home. Mobile drawers use one continuous glass surface. Reduced-transparency preferences and browsers without backdrop filtering get opaque surfaces.

1. **Question:** enter a decision or choose a suggested question. Home submissions continue directly into the start flow.
2. **Agents:** edit names, perspectives, and personality instructions; add or remove agents.
3. **Debate:** read replies, join in, mention an agent, pause/resume, or generate a summary. Enter sends a message, Shift+Enter inserts a new line, and typing `@` filters agents. The composer grows as you type.

Accounts support email-verified registration, sign-in/out, name changes, verified email changes, and password updates/recovery. Passwords are hashed with Werkzeug scrypt; sessions are revocable and stored server-side. Verification codes expire after 10 minutes, allow five attempts, and can be used once. Account writes require CSRF tokens and rate limits apply to login and verification. Production uses secure HttpOnly cookies. Configure `RESEND_API_KEY`, `RESEND_FROM_EMAIL` (a verified sender), and a persistent `GIZMOAPP_SECRET_KEY` on the server, never in browser code or Git. Production deployments must run the existing database migration command before starting with automatic migration disabled.

Conversations are private and account-owned. Recent history, full-text search, titles, archive status, drafts and summaries are stored on the server and available on another signed-in device. Recent keeps creation order. Share creates a separate, revocable view-only token; viewers follow saved replies every five seconds and cannot edit, run agents, spend credits, or see unsent drafts. Creating a new link invalidates the previous link. This is shared watching, not simultaneous multiplayer. The same ownership checks also protect the earlier discussion API.

Pre-ownership sessions keep their previous capability URLs as read-only legacy links. The original browser imports its known legacy records as idempotent private copies into the signed-in account, retaining timestamps, attachments and retrieved sources; it never claims somebody else’s account-owned session. Unlisted legacy links offer Save a private copy. Old originals remain intact for recovery. In the imported copy’s Share dialog, Turn off the older link retires the original capability URL without deleting the private copy. Deleting the imported copy also retires that older link. Original legacy links otherwise remain read-only and are not silently mistaken for the new share token.

Registration includes a live initial-avatar color preview; users can change their saved color in Account settings. New agents receive a random palette color, adjustable during setup and in Add/Edit agent. Colors persist with accounts and debate participants and are reused throughout the app. Database migration 10 adds the account avatar color without changing existing account credentials.

Guests see a Log in action and no Recent list. Authentication is required for group generation, new saved sessions, AI replies, and summaries. Starting from the question box opens the login card and preserves the question; after successful login the requested action continues.

Signed-in accounts have Invite friends and Usage controls. Each completed live AI reply consumes one allowance; failed, interrupted, and empty replies are refunded. Group setup and summaries are included. New accounts receive 50 replies by default (`GIZMO_ACCOUNT_STARTER_REPLIES`). A valid invitation grants **100 extra replies to each person** only when a new invitee verifies their email (`GIZMO_ACCOUNT_INVITE_REWARD`). Existing accounts, self-invitations, and replayed verifications earn no extra reward. Rewards, balances, and usage entries are server-side; transactional reservations prevent parallel requests from overspending. Database migration 9 adds the allowance ledger and referrals. The legacy analysis runner’s public run/continue routes are retired (410); saved discussions and exports remain available. All current agent generation uses the live-reply allowance meter.

The invitation dialog sends to up to five comma-separated email addresses through Resend. The live site uses `GIZMOAPP_PUBLIC_URL=https://debate-agora.beringtech.com`; for a separate deployment, use its own public origin without an application path. The configured URL prefix is appended automatically. On a public deployment the request origin is used if this setting is omitted. Local/private origins are rejected for outgoing invitations. Migration 11 stores pending, failed, and accepted invitations; retrying a failed delivery preserves its provider idempotency key and payload. Only the inviter can view their delivery records. Already-registered addresses are skipped before delivery, including on retries; mixed batches report those addresses while sending to eligible new recipients. Sending an email never grants usage; the existing verified registration transaction grants the reward once.

Account emails share the table-based templates in `server/gizmoapp_server/templates/emails/`: registration, email-change verification, password reset, and invitations. The black transparent wordmark and selected blue banner are embedded as CID PNG/JPEG attachments, with a solid blue header fallback and an Outlook VML background; essential styles are inline and small-screen adjustments are in the email stylesheet. Primary buttons retain the app’s brand blue, `#246cf6`. All messages include a plain-text alternative. Verification codes remain contiguous for copying, and invitation buttons preserve the referral URL, with a visible fallback link. Existing outbox payloads remain immutable for idempotent retries; newly created invitations use the current template. Run `.venv/bin/python scripts/preview_account_emails.py` to regenerate the safe sample HTML files in `design/emails/`; the sample code and referral are not live credentials, and this command never sends email. Preview HTML uses embedded data images solely for browser inspection. Implementation references: [Resend inline images](https://resend.com/docs/dashboard/emails/embed-inline-images) and [Gmail CSS support](https://developers.google.com/workspace/gmail/design/css).

Sources displays server-retrieved web citations, deduplicated by URL, with the agents that cited each source. The official OpenAI connection provides real search and reading tools; see Agent web tools below. A retrieved source is evidence to evaluate, not an automatic fact check, and shared-model agreement is not independent corroboration.

The composer’s **+** opens image upload (at most six images); the paperclip opens file upload. Attachments appear above the text as removable image thumbnails or document tiles, remain attached to the sent message, and survive session reloads. Drag/drop and pasted image files use the same upload path. File contents are never pasted into the textarea. Supported formats: PNG/JPEG/WebP/GIF, PDF, TXT/MD/CSV/JSON, DOCX/XLSX/PPTX; at most six documents and 20 MiB per file. Uploads use multipart requests; originals, previews, and metadata are retained in SQLite rather than the public static directory. Uploads require CSRF protection and have rate/storage limits. Removing an unsent attachment saves the draft first and discards the unreferenced file. Saved attachment links track current messages, queues and drafts. Unlinked uploads abandoned for 24 hours are automatically collected on the next request (at most once per hour); linked files never expire. Attachment access requires upload ownership, ownership of a linked session, or a valid viewing link to a message containing that attachment. Shared viewers cannot inspect draft attachments, and revoking the viewing link also revokes its attachment access.

Document text is extracted server-side (up to 16,000 characters per file); agent requests use up to 24,000 characters of recent file context and six recent images. Scanned/encrypted PDFs may have no extractable text. Image requests use the existing Chat Completions provider’s multimodal input format and require an image-capable model; no provider/model is silently substituted. Actual model calls still require the existing `GIZMO_LLM_*` settings. The 2,000-character limit applies only to typed message text. The upload route permits 20 MiB plus multipart overhead; configure any reverse proxy to allow at least 21 MiB on `/api/debate/attachments` (including your URL prefix). Share also offers a Markdown transcript download.

For direct OpenAI access, set `GIZMO_LLM_API_KEY`, `GIZMO_LLM_BASE_URL=https://api.openai.com/v1`, and `GIZMO_LLM_MODEL=gpt-6-luna` in the server environment or the ignored local `.env`. Restart the server after changes. Process environment values take precedence over local settings. Credentials stay server-side and are excluded from client configuration. Plain chat and summary requests use the existing provider compatibility handling. Native agent web turns use Responses with a small reasoning budget and bounded web tools.

The interface uses the existing Flask application and requires no frontend build service. For a self-hosted deployment, select the text shell with `GIZMOAPP_SHELL=text`; existing chat endpoints and model settings remain supported. The deployment installer generates a 21 MiB upload location while keeping the default request limit at 1 MiB.

The product homepage is available at `/` and the workspace at `/app` (under the configured URL prefix). Its question box and example questions continue to the app and begin after sign-in; example conversations are illustrative and do not call the model. Agent setup and conversations use `/app/participants` and `/app/debate`. Legacy `/home`, `/participants`, `/debate`, and invitation links redirect to their current destinations.

## Inputs and Outputs

**User inputs:** question (required), background/context (optional), confirmed list of roles, follow-up messages (with optional `@mention`), Pause/Resume, Summarize.

**Outputs:** a list of suggested roles, ordered discussion messages with each speaker's role shown, and a short summary of options, pros and cons, and disagreements.

## Technology

- Web app prototype
- LLM service accessed through an API, used to suggest roles, generate replies, and write summaries
- Each participant's replies are guided by the app's discussion instructions, the user-confirmed roles, and earlier messages in the current discussion

## Local setup and validation

Use Python 3.12 and Node.js for validation (verified with Node 24). Node is not required to serve the application, and there is no frontend package installation or build step.

```sh
cp .env.example .env
# Set your model credentials and verified email sender in .env.
ALLOW_NETWORK_INSTALL=1 ./scripts/install_checkout.sh
ALLOW_SERVER_RUN=1 make dev-text
```

Open `http://127.0.0.1:8001/`. The installer creates the Python environment and initializes the database; it never uploads the local `.env` file.

Run `make validate` before release. It checks JavaScript with Node's parser, runs the Python suite against temporary databases and mocked providers, and runs the frontend behavioral suite. Two historical external-tool checks skip explicitly when their optional fixtures are absent.

The production service uses `deploy/gunicorn.conf.py`: one threaded worker by default, with time for streaming requests to finish during reloads. Configure a persistent secret, production cookies, model/email credentials and the public URL on the server. Run the database migration command before starting with automatic migration disabled; migration 15 adds durable reply commits. Keep database backups outside Git. Enable proxy trust only behind the trusted reverse proxy. The live site is deployed separately from GitHub; pushing a commit does not automatically deploy it. See [production deployment notes](docs/deployment.md).

## Limitations and Responsible Use

- Participants are **AI simulations**, not real people. They do not replace feedback from actual teammates, users, or experts.
- The model may give incorrect information. Replies are meant to point out assumptions and claims that need checking, so verify important claims yourself.
- Similar role descriptions can lead to repetitive replies. Edit the roles to get more distinct viewpoints.
- Missing context can cause repeated or inconsistent replies. Restate important details if needed.
- If an API call fails, the existing discussion is kept and you can retry.
- If you change a shared plan based on the discussion, classmates, colleagues, or end users may be affected, so check with them.

Lucide icons are bundled under their ISC license (`server/gizmoapp_server/static/app/text/vendor/LUCIDE-LICENSE`); icons and the logo require no external requests at runtime.

### Agent web tools

On the official OpenAI connection, agent replies use Responses with live `web_search`. The selected agent can answer directly, search, and open/read pages within the same turn; explicit lookup/current-information requests require a tool call unless fresh evidence already exists for that user request. `GIZMO_WEB_SEARCH_ENABLED` defaults to true; `GIZMO_WEB_SEARCH_MAX_CALLS` defaults to 2 (allowed 1–4). Non-OpenAI gateways retain the plain-text adapter and do not gain search merely by enabling the flag.

Sources are session-scoped records created only from hosted search annotations. Other agents receive recent source references as untrusted response excerpts and can reuse or challenge them. Citations are clickable, persisted, and checked against the session source registry when saving. Legacy supplied URLs remain stored as reference text but are not displayed as retrieved web evidence. Private attachments stay in the existing context pipeline. Search queries are instructed to contain only minimal public-topic terms.

Tool failures before text fall back to a short reply that explicitly says online verification was unavailable. Partial failures never become successful replies. Search/planning calls do not separately consume the user's reply allowance; only a completed agent contribution consumes one reply, and failures refund the hold. Tests opt into tool execution explicitly and replace its network transport.


## Early-access reply requests and product checks

Initial allowance is 50 replies; a verified referral adds 100 replies to each account. There is no automatic daily refill. At zero, the discussion automatically pauses; Usage offers a text action to request more replies and keeps reading available. Requests are account-scoped and idempotent. Review grants are transactional and exactly once; ordinary users cannot list or approve requests. A completed reply, its saved closing/pause state, and the charge now commit in one SQLite transaction before the response acknowledges success. Failed/interrupted unsaved replies refund their reservation. A paid reply survives a lost completion event or browser refresh; the client attempts recovery before offering Retry. Late draft saves preserve committed replies, and only revisions created by those server appends may be rebased automatically; other tabs’ edits still conflict. A session may hold only one running reservation, including coordinator selection, preventing duplicate calls from concurrent tabs.

Credit review is restricted to the verified `guolinn@student.ubc.ca` account, including server-side authorization. Enable that account on the server:

```sh
.venv/bin/python server/manage.py grant-admin --email guolinn@student.ubc.ca --shell text
```

The operator can then use Profile → Reply requests to choose a grant of 1–1000 replies (50 initially selected) or decline. The server-only commands `credit-requests` and `review-credit-request REQUEST_ID --replies 137 --shell text` provide the same review flow. No administrator privileges are assigned automatically. Keep the same database environment as the running server when using these commands.

Summary’s optional usefulness feedback stores only the account/session relationship and a yes/no answer, with no chat transcript, search terms or message bodies in analytics. `docs/product-validation.md` lists concrete scenarios for internal QA and small user trials. Automated and synthetic model checks establish behavior, not evidence of product-market fit.
