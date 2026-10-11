# Framework extra co-installability

<!-- GENERATED FILE: do not edit by hand. Refresh with
     `python scripts/coinstall_matrix.py` from python/ (#769). -->

Which framework extras resolve together. Each cell is the outcome of
`uv pip compile` for `donkey-kit[<row>,<column>]` on Python 3.12
(Linux) against the newest releases on the day the table was generated:
`yes` resolves, `no` is an upstream conflict the resolver reports as
"No solution found". The diagonal is the extra on its own; the last column
is the extra next to `[all]`.

The nightly `co-installability` job regenerates this table and fails when
it changes, so a `no` here is re-checked against upstream every night.
Floors only, never ceilings (ADR 0007): the SDK does not pin a framework
down to make a pair resolve. Install a `no` pair in separate environments.

|  | `langgraph` | `adk` | `agent_framework` | `openai-agents` | `anthropic` | `crewai` | `llamaindex` | `strands` | `all` |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `langgraph` | yes | yes | yes | yes | yes | yes | yes | yes | yes |
| `adk` | yes | yes | yes | yes | yes | yes | yes | yes | yes |
| `agent_framework` | yes | yes | yes | yes | yes | yes | yes | yes | yes |
| `openai-agents` | yes | yes | yes | yes | yes | yes | yes | yes | yes |
| `anthropic` | yes | yes | yes | yes | yes | yes | yes | yes | yes |
| `crewai` | yes | yes | yes | yes | yes | yes | yes | yes | yes |
| `llamaindex` | yes | yes | yes | yes | yes | yes | yes | yes | yes |
| `strands` | yes | yes | yes | yes | yes | yes | yes | yes | yes |

Every framework extra together with `[all]`: **yes**.

This is uv's resolver. pip's backtracking resolver can give up
(`resolution-too-deep`) on a set uv resolves; when it does, install with
uv or pin the framework versions from a resolution like this one.
