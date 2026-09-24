# Use these docs with your agent

Live

You probably ask your coding assistant before you open a docs site. These docs
are published in the [llms.txt convention](https://llmstxt.org) so an assistant
can read them directly and write **correct, governed** code — the base URL with
no `/v1`, the `client_id` / `client_secret` header pair, the typed
refusal taxonomy — instead of guessing.

## What's published

Three machine-readable artifacts are generated from these pages on every docs
build, so they never drift from what you see here:

| Artifact | What it is | URL |
| --- | --- | --- |
| `llms.txt` | A curated **index** — one link per page, grouped by section. Best when your tool ingests a doc index and fetches pages on demand. | [`/llms.txt`](https://donkey-development-kit.github.io/donkey-development-kit/llms.txt) |
| `llms-full.txt` | **Every page inlined** into one file. Best for pasting straight into an assistant — no fetching required. | [`/llms-full.txt`](https://donkey-development-kit.github.io/donkey-development-kit/llms-full.txt) |
| Per-page `.md` | The raw markdown for any page, served next to its HTML. Append `.md` to any page URL. | e.g. [`/quickstart.md`](https://donkey-development-kit.github.io/donkey-development-kit/quickstart.md) |

  The site is served under a project sub-path today
  (`/donkey-development-kit`), so the files live at
  `https://donkey-development-kit.github.io/donkey-development-kit/llms.txt`
  rather than a bare domain-root `/llms.txt`. Use the full URLs above.

## Point your assistant at them

Ask Claude Code to read the full docs, then build:

```text
Read https://donkey-development-kit.github.io/donkey-development-kit/llms-full.txt,
then write a governed LangGraph model call using the donkey-kit SDK.
```

In Cursor, add the docs as a source (**Settings → Features → Docs → Add**) with
the index URL, then `@Docs` it in chat:

```text
https://donkey-development-kit.github.io/donkey-development-kit/llms.txt
```

For any assistant, paste the contents of `llms-full.txt` into the conversation,
then ask your question. Because every page is inlined, the assistant has the
full context without following links:

```text
<paste the contents of
 https://donkey-development-kit.github.io/donkey-development-kit/llms-full.txt>

Now write a governed OpenAI Agents SDK setup that handles a TokenBudgetExceeded
refusal.
```

If your tool follows a doc index, give it `llms.txt`. Each entry links to the
page's `.md`, so the tool fetches only the pages it needs:

```text
https://donkey-development-kit.github.io/donkey-development-kit/llms.txt
```

  These docs cover both live capabilities and ones on the [Roadmap](https://donkey-development-kit.github.io/donkey-development-kit/roadmap.md).
  Roadmap pages show the planned API, which is not callable yet. When you ask an
  assistant to write code, tell it to use only capabilities marked **Live**, and
  review generated code before running it against a real gateway.
