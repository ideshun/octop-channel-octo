# Offline validation evidence

Validated against installed distributions:

- Octop 1.0.2b9
- octop-gateway 1.0.3
- octop-harness 1.0.4
- websockets 15.0.1
- cryptography 49.0.0

Final checks: **44 pytest tests passed** (7.69 seconds); `ruff check` passed;
`ruff format --check` passed (19 Python files). The original 25 tests remain.

The host smoke test uses the real PluginContext, manifest, loader and Octop
plugin-channel bridge with real gateway registration in a disposable subprocess
and temporary OCTOP_HOME. Registration exists only in that subprocess's memory.
A separate `/app/.venv/bin/python` smoke loaded the extracted distribution ZIP
and applied its channel through the installed router. No channel start or bot
connection was made. Network tests use local loopback HTTP and mocked DNS only.

Coverage added: invalid URL schemes/credentials/fragments, private DNS rejection,
explicit private-host permission, redirect refusal without second-hop token
forwarding, UI string config validation, event compaction retaining cursor and
unresolved rows, isolated installed-host loading, deterministic allowlisted ZIP.

Final ZIP: `dist/octop-channel-octo-0.1.0.zip`

SHA-256: `4fd9b227427e20e45c2554174abb64ceaa2fd58cf4451b1fb291868785a80571`

This evidence file is intentionally not included in the ZIP (avoids embedding a
self-referential archive checksum). Build output, test environment, upstream raw
evidence and extracted smoke installation all stay under assistant/generated.
No production configuration was edited. No commit or push was performed.

Acceptance still requiring a separate real-server environment: live WS/REST and
media interoperability, server replay retention, load behavior and migration.
Inbound URLs passed to host media consumers remain subject to those consumers'
own download policy; the plugin does not control their DNS or redirect handling.
Message tombstones and unresolved events deliberately stop acceptance at capacity
rather than losing deduplication or uncertain state.
