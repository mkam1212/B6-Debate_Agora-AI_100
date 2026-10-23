# Product validation before public launch

The initial use case is a real decision with competing priorities: help someone discover a missed consideration, understand a trade-off, or identify a small next test. Product, Work and Life remain examples of this same job.

## Observe 5–8 representative people

Ask each person to bring a real, non-sensitive decision. Do not demonstrate the interface first. Observe the first useful contribution, confusion during sign-in/start, whether they need persona editing, and what they do when credits end. Ask them to state what they learned and what they would do next. Record their words with permission rather than interpreting time spent as success.

A successful session should provide a concrete new consideration, distinguish evidence from assumptions, and leave the person with an understandable next step or a narrower unresolved question. More messages, forced disagreement and unanimous agents are not success criteria.

## Release checks

- Home → sign-in → generated agents → streamed conversation, without a second topic submission; Customize agents first still opens setup.
- Ask a practical choice (free workshop vs small fee), an opinion (meeting frequency), and a factual lookup. Roles should address the same actual claim through different stakes, acknowledge uncertainty, and update when the user corrects a premise.
- At three saved AI replies, a checkpoint becomes available without stopping the conversation. Open Summary while another reply is running, reload, and verify the saved outcome and its stale state.
- Use a separate signed-in browser with no local storage. Its history should load from the account; another account must not see it. Rename must not rewrite the opening question. Archive, restore and delete should have predictable results.
- Open a viewing link as a guest. Confirm no composer, agent editing, pause/resume, generation, quota use or draft visibility. Revoke/replace the link and confirm both conversation and attachment access stop.
- Verify zero-credit reading, referral +100 each once, a pending credit request, operator-only review, and a repeated approval producing no second grant.
- Exercise mobile 390×844 and desktop, keyboard-only use, reduced motion, upload/model failures, long names/titles and empty search.

## Remaining real-world evidence

A synthetic transcript cannot establish that real people make better decisions or return to the product. Run these trials and review optional Summary feedback before expanding scope or promising simultaneous collaboration.


## Verification recorded on 2026-10-21

- 221 related Python checks and 40 frontend checks passed, including account boundaries, cross-browser history, share rotation/revocation, attachment visibility, legacy private copies, summary persistence, credit review and duplicate grants.
- An isolated real-provider session completed four AI turns and a summary. The user corrected a funding assumption midway; later turns and the summary retained that correction and left costs/demand unresolved. Main-account credit was not used.
- Browser checks covered direct start from a suggested question, optional role setup, guest view-only reading, an allowance request and operator approval, plus desktop/mobile layout.
- The repository-wide discovery run also found preexisting missing scaffolding/deployment/fixture files and local-socket sandbox restrictions. Its no-Node structural checker misreads existing JavaScript regular expressions. These are not reported as a fully passing repository suite; the changed JavaScript passed Node syntax and behavioral checks.
- User interviews and actual decision outcomes remain to be gathered; the test transcript is not a substitute for them.

## Follow-up refinement, 2026-10-21

- Default three-agent rounds now use an opening, focused exchange, and three distinct closing turns, with a nine-reply limit. The closing marker is saved with the final reply, so refreshing cannot silently restart a completed round. New input or explicit Resume starts a new round. Other group sizes stay within 6–12 replies.
- The coordinator includes the complete current round and restricts candidates to prevent consecutive automatic turns from one speaker and repeated closing statements. Explicit user mentions remain authoritative. Reply streaming still uses a single participant model stream, with no post-generation rewrite or duplicated completion.
- Two isolated real-provider rounds on AI in creative work completed nine replies and persisted summaries. The final run selected Maya, Owen and Nina without consecutive repeats, challenged consent/copying/replacement distinctions, and recorded specific concessions. No main-account credit was spent. These samples show improved interaction, not a guarantee that every discussion reaches a sound outcome.
- Browser checks confirmed automatic summary opening, a saved zero-credit pause after the last reply, New debate during an active stream and from an unavailable session, account-only reviewer UI, and sign-out removing the conversation and Recent in both open test tabs. Desktop and 390×844 mobile layouts were checked.
- Credit-review authorization is restricted to the designated school email even when a stale database grant exists for another account. The local preview database retains only the designated reviewer grant.
- Usage and invite empty states are shorter; the credit explanation and early-access review copy are removed. Customize agents is now a quiet action inside the composer; feedback and allowance requests use text actions. The summary avatar stack is larger and properly overlaps, and recovery content and its retry action are centered together.

- Final related checks: 229 Python tests and 40 frontend tests passed (269 total). Node syntax checks and whitespace validation passed. The previously documented repository-wide baseline issues remain separate.

## Allowance and icon update, 2026-10-21

- New verified registrations receive 50 replies. Each successful verified invitation adds 100 replies to the inviter and invitee, once. Existing ledger entries retain their original amounts.
- The designated reviewer can edit a request's grant before approval (1–1000 replies, initially 50). Server validation rejects non-integers and out-of-range amounts. Approval remains account-restricted, transactional, and idempotent.
- Customize agents now uses a person/edit SVG in both states, with blue icon color and a light blue selection treatment when on. The accessible toggle state and optional pre-debate setup behavior are retained.
- The starter-allowance sentence has been removed from registration.

- This update passed 231 related Python tests and 40 frontend tests. An isolated browser approval granted 137 replies exactly, retained the second request’s edited amount, and declined that second request without a grant. Both icon states and the registration copy were visually checked.

## Durable delivery and repository packaging, 2026-10-21

- Registered email addresses are skipped before invitation delivery, including retries. A mixed batch sends only to eligible recipients and reports skipped addresses in the form.
- Reply content, closing/pause state and usage commit atomically before acknowledgement. Lost completion events can recover the saved reply. Late draft saves preserve paid replies; unrelated edits from other tabs still conflict. Partial failures refund the unused hold. Legacy run/continue endpoints return 410, retaining saved-history access.
- Explicitly removed unreferenced uploads are discarded after the draft save. Attachment links track current messages, queues and drafts. Abandoned unlinked uploads expire after a 24-hour grace period; saved references are retained.
- Isolated browser checks verified mixed-batch invitations, streaming while typing, manual pause, refresh recovery and removing an attachment without losing the text draft. Delivery was mocked and no main-account credits were used.
- The final feature regression passed 251 Python checks and 40 frontend checks. Repository packaging adds safe environment examples, threaded Gunicorn startup settings, focused code-map documentation and runtime/artifact exclusions.
- Full repository validation passed: 313 Python checks ran with two explicit skips for unavailable optional historical tools/fixtures, plus 40 frontend checks. JavaScript now uses Node's actual parser rather than the previous structural checker that misread valid regular expressions. No production deployment or real-user outcome validation is implied by this upload.

## Conversation organization and mobile refinement, 2026-10-23

- Both Add agent entry points use the same editor. Question-tailored templates preserve edits made while generation is pending, ignore responses after closing, and consider existing participants when suggesting a new perspective.
- Private folders support creation, renaming, moving conversations and removal without deleting conversations. Folder membership does not alter conversation revisions, reply delivery, sharing or AI context. Ordering follows conversation creation time, with independent pagination for each list.
- Workspace navigation changes conversations without reloading the document, retains drafts and reading positions, and rejects stale asynchronous responses after navigation or sign-out.
- Desktop and mobile checks covered long folder names, compact selection pills, interrupted expand/collapse animations, reduced motion, narrower drawers and a bounded folder control in the composer. Mention selection uses the established solid blue treatment.
- Release validation passed: 323 Python tests ran with two explicit skips for unavailable optional historical tools/fixtures; all 75 frontend tests passed. Tests use temporary databases and mocked providers, without real-user credits or email delivery.
- Database migration 16 adds private folders and nullable conversation membership. Its regression test upgrades a version-15 database while preserving existing conversation drafts.
- Web-tool tests explicitly select a mocked model, retaining the production checkout's test-isolation fix without depending on local provider configuration.
