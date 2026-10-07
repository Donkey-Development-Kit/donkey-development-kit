# Request rate limiting — the rest of the LIVE sequence (#974)

Same probe as `python/src/donkey_kit/simulator/_fixtures/anypoint/request_rate_limit/`
(read its README for the provenance: 2026-10-07, instance `21188400`,
`rate-limiting` 1.5.1, 3 requests / 60000 ms per `client_id`). The simulator
serves the first 429 from there. These are the calls a test needs but the
simulator does not serve:

- `responses.success.headers.txt` / `.body.json`: the 1st 200,
  `x-ratelimit-remaining: 2`, with Azure's own `x-ratelimit-*-requests` /
  `-tokens` passthrough beside the gateway trio.
- `responses.window-exhausted.headers.txt`: the 3rd 200, `x-ratelimit-remaining: 0`.
  The call after it is the one `pace()` must refuse without sending.
- `reject.request-rate-limit.repeat.headers.txt`: the 5th call, a second 429 in
  the same window (`x-ratelimit-reset` still counting down).
- `responses.after-reset.headers.txt`: the first 200 of the next window,
  `x-ratelimit-remaining: 2` again.
