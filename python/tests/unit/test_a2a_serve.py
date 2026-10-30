"""``donkey.serve()`` — the A2A listener wrapping the official ``a2a-sdk``
(BG §2.9.1, #224).

Every round trip is raw JSON-RPC over ``httpx.ASGITransport``: the caller uses
no ``a2a-sdk`` client, so these tests pin the wire contract a partner agent in
any language sees, not just what the SDK's own client tolerates.
"""

from __future__ import annotations

import asyncio
import builtins
import logging
import subprocess
import sys
import warnings
from typing import Any

import httpx
import pytest

pytest.importorskip("a2a", reason="needs the [a2a] extra")

from donkey_kit import A2AResult, A2ATask, Donkey
from donkey_kit.a2a import _server as a2a_server
from donkey_kit.a2a import build_app, serve
from donkey_kit.core.errors import ConfigWarning

_CARD = "/.well-known/agent-card.json"


def _send(text: str | None = None, data: Any = None) -> dict[str, Any]:
    parts: list[dict[str, Any]] = []
    if text is not None:
        parts.append({"text": text})
    if data is not None:
        parts.append({"data": data})
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "SendMessage",
        "params": {"message": {"messageId": "m1", "role": "ROLE_USER", "parts": parts}},
    }


async def _call(handler: Any, body: dict[str, Any]) -> dict[str, Any]:
    app: Any = build_app(handler)
    transport = httpx.ASGITransport(app=app)
    # Inside the lifespan, as uvicorn runs it: shutdown drains the SDK's tasks.
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://agent") as client,
    ):
        response = await client.post("/", json=body, headers={"A2A-Version": "1.0"})
    assert response.status_code == 200, response.text
    payload: dict[str, Any] = response.json()
    assert "error" not in payload, payload
    task: dict[str, Any] = payload["result"]["task"]
    return task


def _echo() -> Any:
    @Donkey.agent(name="echo", skills=["echo", "shout"])
    async def echo(task: A2ATask) -> str:
        """Echoes the caller's text back."""
        return f"echo: {task.input}"

    return echo


async def test_card_is_generated_from_the_agent_marker() -> None:
    transport = httpx.ASGITransport(app=build_app(_echo(), url="https://gw.example/echo/"))
    async with httpx.AsyncClient(transport=transport, base_url="http://agent") as client:
        card = (await client.get(_CARD)).json()
    assert card["name"] == "echo"
    assert card["description"] == "Echoes the caller's text back."
    assert [s["id"] for s in card["skills"]] == ["echo", "shout"]
    assert card["supportedInterfaces"][0]["url"] == "https://gw.example/echo/"
    assert card["supportedInterfaces"][0]["protocolBinding"] == "JSONRPC"


async def test_a_str_result_completes_the_task_with_a_text_artifact() -> None:
    task = await _call(_echo(), _send("hello"))
    assert task["status"]["state"] == "TASK_STATE_COMPLETED"
    assert task["artifacts"][0]["parts"] == [{"text": "echo: hello"}]


async def test_an_a2a_result_carries_text_and_data_artifacts() -> None:
    seen: list[A2ATask] = []

    @Donkey.agent(name="totals")
    async def totals(task: A2ATask) -> A2AResult:
        """Sums the numbers it is sent."""
        seen.append(task)
        return A2AResult(text="done", data={"sum": sum(task.data[0]["n"])})

    task = await _call(totals, _send("add these", {"n": [1, 2, 3]}))
    assert task["status"]["state"] == "TASK_STATE_COMPLETED"
    assert [a["parts"] for a in task["artifacts"]] == [[{"text": "done"}], [{"data": {"sum": 6}}]]
    assert seen[0].input == "add these"
    assert seen[0].task_id == task["id"]
    assert seen[0].context_id == task["contextId"]


async def test_a_sync_handler_runs_in_a_worker_thread() -> None:
    @Donkey.agent(name="sync-echo")
    def sync_echo(task: A2ATask) -> str:
        """Echoes, synchronously."""
        return task.input.upper()

    task = await _call(sync_echo, _send("quiet"))
    assert task["artifacts"][0]["parts"] == [{"text": "QUIET"}]


async def test_a_raising_handler_fails_the_task_with_the_type_name_only(
    caplog: pytest.LogCaptureFixture,
) -> None:
    @Donkey.agent(name="broken")
    async def broken(task: A2ATask) -> str:
        """Always fails."""
        raise RuntimeError("db password is hunter2")

    with caplog.at_level(logging.ERROR, logger="donkey_kit.a2a._server"):
        task = await _call(broken, _send("hi"))
    assert task["status"]["state"] == "TASK_STATE_FAILED"
    text, error = task["status"]["message"]["parts"]
    assert text["text"] == "the agent failed: RuntimeError"
    assert error["data"]["error"]["type"] == "RuntimeError"
    assert error["data"]["error"]["refusal"] is False
    assert "hunter2" not in str(task)
    assert "hunter2" in caplog.text  # the detail goes to the log, never the caller


async def test_a_result_that_is_not_str_or_a2a_result_fails_the_task() -> None:
    @Donkey.agent(name="wrong-type")
    async def wrong(task: A2ATask) -> Any:
        """Returns the wrong type."""
        return 42

    task = await _call(wrong, _send("hi"))
    assert task["status"]["state"] == "TASK_STATE_FAILED"
    assert task["status"]["message"]["parts"][0]["text"] == "the agent failed: TypeError"


def test_an_unmarked_handler_is_rejected() -> None:
    async def plain(task: A2ATask) -> str:
        return ""

    with pytest.raises(TypeError, match="not marked with @donkey.agent"):
        build_app(plain)


def test_a_missing_extra_names_the_install_command(monkeypatch: pytest.MonkeyPatch) -> None:
    real_import = builtins.__import__

    def no_a2a(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "a2a" or name.startswith("a2a."):
            raise ImportError(f"No module named {name!r}", name="a2a")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_a2a)
    with pytest.raises(ImportError, match=r'pip install "donkey-kit\[a2a\]"'):
        build_app(_echo())


def test_serve_binds_to_localhost_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    import uvicorn

    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: calls.append(kw))
    serve(_echo())
    assert calls == [{"host": "127.0.0.1", "port": 8000}]


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1", "127.0.0.2"])
def test_a_loopback_bind_does_not_warn(monkeypatch: pytest.MonkeyPatch, host: str) -> None:
    import uvicorn

    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: None)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        serve(_echo(), host=host)


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "10.0.0.5", "agent.internal"])
def test_a_non_loopback_bind_warns_that_the_gateway_belongs_in_front(
    monkeypatch: pytest.MonkeyPatch, host: str
) -> None:
    import uvicorn

    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: calls.append(kw))
    with pytest.warns(ConfigWarning, match="authenticates no caller") as record:
        serve(_echo(), host=host, port=9002)
    assert host in str(record[0].message)
    assert "gateway" in str(record[0].message)
    assert calls == [{"host": host, "port": 9002}]  # a warning, not a refusal


async def _get_task(client: httpx.AsyncClient, task_id: str) -> dict[str, Any]:
    body = {"jsonrpc": "2.0", "id": 2, "method": "GetTask", "params": {"id": task_id}}
    response = await client.post("/", json=body, headers={"A2A-Version": "1.0"})
    payload: dict[str, Any] = response.json()
    return payload


async def test_the_oldest_finished_task_is_dropped_past_the_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(a2a_server, "_MAX_FINISHED_TASKS", 2)
    app: Any = build_app(_echo())
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://a") as c,
    ):
        ids = []
        for n in range(3):
            response = await c.post("/", json=_send(f"t{n}"), headers={"A2A-Version": "1.0"})
            ids.append(response.json()["result"]["task"]["id"])
        oldest, *kept = [await _get_task(c, task_id) for task_id in ids]
    assert "error" in oldest, oldest
    assert [k["result"]["artifacts"][0]["parts"] for k in kept] == [
        [{"text": "echo: t1"}],
        [{"text": "echo: t2"}],
    ]


async def test_a_running_task_is_never_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(a2a_server, "_MAX_FINISHED_TASKS", 1)
    release = asyncio.Event()
    started: list[str] = []

    @Donkey.agent(name="gate")
    async def gate(task: A2ATask) -> str:
        """Waits on the first task; answers the others at once."""
        if task.input == "wait":
            started.append(task.task_id)
            await release.wait()
        return task.input

    app: Any = build_app(gate)
    headers = {"A2A-Version": "1.0"}
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://a") as c,
    ):
        waiting = asyncio.create_task(c.post("/", json=_send("wait"), headers=headers))
        while not started:
            await asyncio.sleep(0)
        for n in range(3):
            await c.post("/", json=_send(f"done {n}"), headers=headers)
        running = await _get_task(c, started[0])
        release.set()
        await waiting
    assert running["result"]["status"]["state"] == "TASK_STATE_WORKING"


def test_donkey_serve_delegates_with_its_governance_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import donkey_kit.a2a
    from donkey_kit.core.config import DonkeyConfig
    from donkey_kit.core.refusals import TypedRefusals

    calls: list[tuple[Any, dict[str, Any]]] = []
    monkeypatch.setattr(donkey_kit.a2a, "serve", lambda h, **kw: calls.append((h, kw)))
    handler = _echo()
    donkey = Donkey(DonkeyConfig(telemetry=False, correlation_header="X-Run-Id"))
    donkey.serve(handler, port=9001)
    ((got, kwargs),) = calls
    assert got is handler
    assert isinstance(kwargs.pop("refusals"), TypedRefusals)
    assert kwargs == {
        "host": "127.0.0.1",
        "port": 9001,
        "url": None,
        "version": "0.0.0",
        "telemetry": False,
        "correlation_header": "X-Run-Id",
    }


def test_importing_donkey_kit_does_not_load_the_a2a_sdk() -> None:
    code = (
        "import sys, donkey_kit\n"
        "donkey_kit.A2ATask\n"
        "import donkey_kit.a2a\n"
        "assert not any(m == 'a2a' or m.startswith('a2a.') for m in sys.modules), 'a2a loaded'\n"
    )
    subprocess.run([sys.executable, "-c", code], check=True)
