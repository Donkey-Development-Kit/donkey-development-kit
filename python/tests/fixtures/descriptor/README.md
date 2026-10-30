# Captured fixtures — descriptor-derivation attribute reads (BG §2.5)

Captured 2026-10-26 (#1024) by `scripts/capture_descriptor_fixtures.py --write`,
one framework per environment, each at the version its
`constraints/adapter-contract-<framework>-py3.12.txt` lock pins. These are the
`docs/verified-apis.md` §10 rows, recorded as data.

Each file is one real bound tool built from the script's reference function
`lookup_employee(employee_id: str, include_manager: bool = False)`. It holds:

- `distribution` / `version` / `python` / `captured`: what was installed;
- `object`: the class of the tool the framework built;
- `reads`: every §10 attribute read, keyed by the expression that read it;
- `descriptor`: the `{"tools": [{"name", "description", "inputSchema"}]}` those
  reads make, which `registry/introspect.py`'s `derive_descriptor()` must equal.

`mcp-1.json` and `mcp-2.json` are the two majors of the MCP Python SDK in the
supported range (`FastMCP` in 1.x, `MCPServer` in 2.x). Every other file is
named for the framework key in the script.

`tests/unit/test_descriptor_derivation.py` compares against a file only when the
installed version equals its `version`. At any other version (the nightly
matrix's latest) it still checks the declared shape and the script's
independent read. After recapturing, relock:
`python -m donkey_kit.simulator.fixtures --relock`.
