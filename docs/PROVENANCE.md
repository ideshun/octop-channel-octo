# Upstream provenance

Upstream: https://github.com/Mininglamp-OSS/openclaw-channel-octo/tree/b6d6fdf83e6f186a61044eb5bba7d727ea762b21

Verified from public GitHub sources at the pinned revision below.

- `LICENSE` SHA-256: `c71d239df91726fc519c6eb72d318ec65820627232b2f796219e87dcf35d0ab4`.
- `NOTICE`: HTTP 404 (not present at this revision).
- `src/socket.ts` SHA-256: `9928f49ddcdad56e1c89ccbe38141d3100c2953c719143d3dc6ca5d16980f216`.
- `src/types.ts` SHA-256: `98c270b35a80d24882a365a55df929550bf059f8c52f6c8b7558ae30c9a0a243`.
- `src/api.ts`: HTTP 404 (not present at this revision).

The preview explicitly identifies socket.py as a Python port of upstream src/socket.ts.
It implements the same packet constants, CONNECT/CONNACK, X25519, AES-CBC, RECV and ACK wire layout.
This release changes that port with cryptographic randomness, bounded frames, handshake deadlines,
reconnection identity refresh, durable-before-ACK delivery, and destination validation.
api.py uses upstream API endpoint/payload contracts and types.ts constants, with a Python REST transport.
No upstream copyright holder or year is inferred from the repository organization name.
The upstream LICENSE contains the standard Apache appendix placeholder, not a project copyright claim.
The remaining channel adapter, SQLite inbox, configuration, tests and packaging are Octop-specific work.
The preview reports Hermes as design/test reference only; no Hermes modules are included.
Runtime dependencies are not vendored; their licenses remain with their respective distributions.

- API contract reference `src/api-fetch.ts` SHA-256: `e3ecaa61abcfe3f09a0bfcba667ad228dc6b76ef959ab41185c235e3f8fdd8df`.

- API contract reference `skills/octo-bot-api/SKILL.md` SHA-256: `707861fef8ef48129b9118034a9f4aa49565e09493b48e87de8f28cd48a2beee`.
