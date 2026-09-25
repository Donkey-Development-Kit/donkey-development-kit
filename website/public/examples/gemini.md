# Gemini

No Gemini adapter ships, so these are plain `httpx` against a proxy
provisioned **`Format=Gemini`** (for example `ddk-gemini-inbound`) with the
same `client_id` / `client_secret` pair. The route is
`<proxy URL>/models/<model>:generateContent`. `DonkeyConfig` still resolves
and validates the credentials, and `classify()` still types the errors.

Both scripts need a live `Format=Gemini` proxy; there is no offline Gemini
script.

| # | Script | Shows | Needs |
| --- | --- | --- | --- |
| 01 | `native-generate-content.py` | A Gemini-shaped `contents` body, usage and routing | Proxy credentials, `Format=Gemini` |
| 02 | `openai-shape-rejected.py` | An OpenAI-shaped request typed as `UpstreamRequestError` | Proxy credentials, `Format=Gemini` |

## Install

Follow the [examples setup](https://donkey-development-kit.github.io/donkey-development-kit/examples.md#setup) first, then:

```bash
python -m pip install -e "../donkey-development-kit/python[llm]"   # httpx + classify()
set -a; source .env.local; set +a
export DONKEY_LLM_PROXY_URL=https://<host>/ddk-gemini-inbound/      # overrides the file
```

The model is `gemini-2.5-flash` (the `MODEL` constant).

## 01 — Native `generateContent`

```bash
python "demos/human-made/gemini/01 - native-generate-content.py"
```

```python
cfg = DonkeyConfig.from_env().validated(need="llm")
headers = {"client_id": cfg.llm_proxy_client_id, "client_secret": cfg.llm_proxy_client_secret}

ok = httpx.post(
    f"{cfg.llm_proxy_url}/models/{MODEL}:generateContent",
    headers=headers,
    json={"contents": [{"role": "user", "parts": [{"text": "Say hello in exactly three words."}]}]},
    timeout=60,
)
ok.raise_for_status()
body = ok.json()
print(body["candidates"][0]["content"]["parts"][0]["text"])
print("total tokens ", body["usageMetadata"]["totalTokenCount"])
print("routing      ", ok.headers.get("x-llm-proxy-model-based-routing-success"))
```

**You should see:** the reply, `total tokens` from `usageMetadata`, and the
proxy's model-based `routing` header. A `KeyError: 'candidates'` means the
proxy refused; print `ok.text` to see why. A `404` means the URL points at a
`Format=OpenAI` proxy.

## 02 — The wrong shape, typed

```bash
python "demos/human-made/gemini/02 - openai-shape-rejected.py"
```

An OpenAI-shaped `/chat/completions` request sent to the Gemini proxy comes
back as Gemini's error envelope. `classify()` types it as a bad request, not a
policy refusal.

```python
bad = httpx.post(
    f"{cfg.llm_proxy_url}/chat/completions",
    headers=headers,
    json={"model": "gemini-2.5-flash", "messages": [{"role": "user", "content": "hello"}]},
    timeout=60,
)
error = classify(bad)
print(type(error).__name__, bad.status_code, getattr(error, "code", None), getattr(error, "error_type", None))
print(error)
```

**You should see:** `UpstreamRequestError`, the HTTP status, the upstream code
and error type, and the message. Typing Gemini's list envelope needs
`donkey-kit` 0.1.0.dev9 or later.

**Learn more:** [Typed refusals](https://donkey-development-kit.github.io/donkey-development-kit/errors.md) · [Model access](https://donkey-development-kit.github.io/donkey-development-kit/frameworks.md)

**Source:**
[`demos/human-made/gemini/`](https://github.com/Donkey-Development-Kit/donkey-development-kit-demos/tree/main/demos/human-made/gemini)
