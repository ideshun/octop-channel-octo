# Changelog

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
