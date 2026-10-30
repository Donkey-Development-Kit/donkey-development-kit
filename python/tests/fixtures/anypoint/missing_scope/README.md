# Captured fixtures — missing connected-app scopes (#23)

Captured 2026-10-28 from the real sandbox business group with a throwaway
`client_credentials` connected app that held none of the scopes the call needs
(only the implicit `profile`, or for `exchange_publish` only `read:exchange`).
The app was deleted after the run. `docs/scopes.md` has the full per-operation
table and `docs/verified-apis.md` §1 the ledger rows.

`responses.json` holds, per call the SDK makes, the request, the scope that
made it succeed, and the status and raw body Anypoint answered without it. The
bodies hold no identifiers. `exchange_version` is trimmed: the live body also
had a `details` list repeating the message.

What the captures show: a missing scope has no single shape. Accounts answers
`401` with a plain-text body, API Manager, Gateway Manager, Exchange and API
Governance answer `403`, each with its own body, and the Exchange Graph search
answers `200` with no assets.
