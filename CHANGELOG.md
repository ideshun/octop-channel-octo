# Changelog

## 0.1.3

- Keep durable lifecycle transitions for real gateway messages; quarantine interrupted turns and finalize passive group chatter.
- Disable message batching, preserve sender trust per turn, and bound gateway handoff with optional explicit enqueue receipts.
- Separate group activation from history retention; reserve commands for the owner without replying to unmentioned group commands.
- Deliver bounded, independent permission notices with correct DM/group/topic routing.
- Reuse DNS-pinned HTTP connections, enforce request deadlines and Retry-After, and avoid retrying invalid successful responses.
- Catch up full event pages immediately; sort cursor updates, batch ACK retries with bounded concurrency, and expose component health.
- Add private status, safe archive, retained dedup tombstones, and explicitly confirmed uncertain-work recovery tooling.
- Include optional host compatibility patches, real gateway regressions, Linux/macOS CI and packaged-host smoke tests.
- Document the host-enforced tool permission boundary for non-owner chat.

## 0.1.2

- Publish a regular release containing the changes made after the v0.1.1 preview archive.
- Add matching Chinese/English READMEs with web ZIP/URL installation and Linux/macOS availability.
- Put API URL before Bot Token, clarify mention-only behavior, and link configuration help to this repository.
- Supply help text for configuration tooltips; document the companion host rendering requirement (Octop #2162).
- Include both READMEs and operational documentation in the installation archive.

## 0.1.1

- Restore exact plugin/channel avatar and full plugin description.
- Restore the complete channel form, helpers and defaults, including mention and history controls.
- Automatically persist state under OCTOP_HOME or ~/.octop; keep bot scope stable across token rotation.
- Validate optional absolute state directory overrides; remove the mandatory UI directory field.
- Document b9 dashboard metadata gaps and provide a separate, unapplied compatible host patch.

## 0.1.0

- Standalone channel plugin targeting Octop 1.0.2b9 / gateway 1.0.3 / harness 1.0.4.
- Private/group/topic messages, media sends, access gates and mention context.
- Durable shared WS/poll inbox, commit-before-ACK, retryable REST ACKs, restart quarantine.
- Exact-host self-host allowlist, DNS-pinned network transports, redirect refusal and token isolation.
- Strict UI config parsing; bounded event maintenance preserving unresolved work and cursor.
- Deterministic source-only ZIP builder, upstream Apache attribution and isolated host integration tests.
- No live-bot end-to-end or production migration acceptance performed.
