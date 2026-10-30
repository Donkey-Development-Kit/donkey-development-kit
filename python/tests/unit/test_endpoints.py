"""``core.endpoints``: which hosts count as loopback (#1443), and the refusal of a
user name or password in a configured endpoint URL (#1478).

httpx turns a URL's user name and password into an ``Authorization: Basic``
header for that host, and the SDK echoes some URLs in logs and error messages,
so ``require_secure_url`` refuses any URL that carries them, whatever
``DONKEY_ALLOW_HTTP`` says. Each caller is exercised through its own surface:
the config fields (from the environment, a project file and code), the
connected-app token endpoint, and a URL passed in code to ``allow_endpoint``
or an adapter factory.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import httpx
import pytest

from donkey_kit.core.auth import AnypointConnectedApp
from donkey_kit.core.config import DonkeyConfig
from donkey_kit.core.endpoints import is_loopback, is_loopback_host, require_secure_url
from donkey_kit.core.errors import ConfigError
from donkey_kit.core.transport import DonkeyAsyncClient
from donkey_kit.integrations import ADAPTERS


@pytest.mark.parametrize(
    "host", ["localhost", "LOCALHOST", "127.0.0.1", "127.8.9.10", "::1", "[::1]"]
)
def test_loopback_hosts(host: str) -> None:
    assert is_loopback_host(host)


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "10.0.0.5", "agent.internal", "", "localhost."])
def test_hosts_other_machines_can_reach(host: str) -> None:
    assert not is_loopback_host(host)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("http://localhost:8080/", True),
        ("http://[::1]:8080/", True),
        ("http://0.0.0.0:8080/", False),
        ("https://gw.example.com/", False),
        ("not a url", False),
    ],
)
def test_is_loopback_reads_the_url_host(url: str, expected: bool) -> None:
    assert is_loopback(url) is expected


# --- a user name or password in the URL (#1478) ------------------------------

# The two halves of the URL's userinfo, named neutrally: CodeQL's name heuristic
# would otherwise trace a "password" constant into the token fetch's debug log,
# which the refusal keeps it from reaching.
_FIRST = "alice"
_SECOND = "example-pw"
_LLM_HOST = "llm.example.test"
_CP_HOST = "cp.example.test"


def _with_userinfo(host: str, *, scheme: str = "https", path: str = "/") -> str:
    return f"{scheme}://{_FIRST}:{_SECOND}@{host}{path}"


def _assert_userinfo_refusal(err: ConfigError, *, name: str, host: str) -> None:
    message = str(err)
    assert message.startswith(f"{name} carries a user name or password")
    assert host in message
    assert "Remove the user name and password from the URL" in err.remediation
    for part in (_FIRST, _SECOND):
        assert part not in message
        assert part not in err.remediation


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An empty project dir as cwd and an empty user config dir."""
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    xdg = tmp_path / "xdg"
    xdg.mkdir()
    monkeypatch.chdir(project_dir)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    return project_dir


@pytest.mark.parametrize(
    "url",
    [
        _with_userinfo(_LLM_HOST),
        f"https://{_FIRST}@{_LLM_HOST}/",
        f"https://:{_SECOND}@{_LLM_HOST}/",
    ],
)
def test_require_secure_url_refuses_a_user_name_or_password(url: str) -> None:
    with pytest.raises(ConfigError) as err:
        require_secure_url(url, name="llm_proxy_url")

    _assert_userinfo_refusal(err.value, name="llm_proxy_url", host=_LLM_HOST)


@pytest.mark.parametrize(
    "url",
    [
        _with_userinfo(_LLM_HOST, scheme="http"),
        _with_userinfo("127.0.0.1:8080", scheme="http"),
        _with_userinfo("localhost:8080", scheme="http"),
    ],
)
def test_neither_allow_http_nor_a_loopback_host_lets_userinfo_through(
    url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DONKEY_ALLOW_HTTP", "1")

    with pytest.raises(ConfigError, match="carries a user name or password"):
        require_secure_url(url, name="llm_proxy_url")


def test_an_llm_proxy_url_from_the_environment_with_userinfo_is_refused(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DONKEY_LLM_PROXY_URL", _with_userinfo(_LLM_HOST, path="/openai/"))
    monkeypatch.setenv("DONKEY_LLM_PROXY_CLIENT_ID", "cid")
    monkeypatch.setenv("DONKEY_LLM_PROXY_CLIENT_SECRET", "secret")

    with pytest.raises(ConfigError) as err:
        DonkeyConfig.from_env().validated(need="llm")

    _assert_userinfo_refusal(err.value, name="llm_proxy_url", host=_LLM_HOST)


def test_a_base_url_from_the_environment_with_userinfo_is_refused(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANYPOINT_BASE_URL", _with_userinfo(_CP_HOST))
    monkeypatch.setenv("ANYPOINT_CLIENT_ID", "cid")
    monkeypatch.setenv("ANYPOINT_CLIENT_SECRET", "secret")
    monkeypatch.setenv("ANYPOINT_ORG_ID", "org")

    with pytest.raises(ConfigError) as err:
        DonkeyConfig.from_env().validated(need="control_plane")

    _assert_userinfo_refusal(err.value, name="base_url", host=_CP_HOST)


def test_endpoints_from_a_project_file_with_userinfo_are_refused(project: Path) -> None:
    (project / ".donkey-kit.toml").write_text(
        "[donkey]\n"
        f'llm_proxy_url = "{_with_userinfo(_LLM_HOST, path="/openai/")}"\n'
        f'base_url = "{_with_userinfo(_CP_HOST)}"\n'
        'org_id = "org"\n'
    )
    (project / ".donkey-kit.local.toml").write_text(
        "[donkey]\n"
        'llm_proxy_client_id = "cid"\nllm_proxy_client_secret = "secret"\n'
        'client_id = "cid"\nclient_secret = "secret"\n'
    )
    cfg = DonkeyConfig.from_env()

    with pytest.raises(ConfigError) as llm:
        cfg.validated(need="llm")
    with pytest.raises(ConfigError) as control_plane:
        cfg.validated(need="control_plane")

    _assert_userinfo_refusal(llm.value, name="llm_proxy_url", host=_LLM_HOST)
    _assert_userinfo_refusal(control_plane.value, name="base_url", host=_CP_HOST)


def test_endpoints_set_in_code_with_userinfo_are_refused() -> None:
    cfg = DonkeyConfig(
        llm_proxy_url=_with_userinfo(_LLM_HOST, path="/openai/"),
        llm_proxy_client_id="cid",
        llm_proxy_client_secret="secret",
        base_url=_with_userinfo(_CP_HOST),
        client_id="cid",
        client_secret="secret",
        org_id="org",
    )

    with pytest.raises(ConfigError) as llm:
        cfg.validated(need="llm")
    with pytest.raises(ConfigError) as control_plane:
        cfg.validated(need="control_plane")

    _assert_userinfo_refusal(llm.value, name="llm_proxy_url", host=_LLM_HOST)
    _assert_userinfo_refusal(control_plane.value, name="base_url", host=_CP_HOST)


async def test_the_token_endpoint_with_userinfo_is_refused_before_posting() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        auth = AnypointConnectedApp(
            client_id="cid",
            client_secret="secret",
            control_plane_url=_with_userinfo(_CP_HOST, path=""),
            http_client=http_client,
        )
        with pytest.raises(ConfigError) as err:
            await auth.token()

    _assert_userinfo_refusal(err.value, name="token endpoint", host=_CP_HOST)
    assert seen == []


def _llm_cfg() -> DonkeyConfig:
    return DonkeyConfig(
        llm_proxy_url="https://proxy.example.internal/openai/",
        llm_proxy_client_id="cid",
        llm_proxy_client_secret="secret",
    )


async def test_allow_endpoint_refuses_a_url_with_userinfo() -> None:
    cfg = _llm_cfg()
    http = DonkeyAsyncClient(cfg, None)
    before = set(http.checked_origins)
    try:
        with pytest.raises(ConfigError) as err:
            http.allow_endpoint(
                _with_userinfo("other.example.internal", path="/v1/"), name="base_url"
            )
    finally:
        await http.aclose()

    _assert_userinfo_refusal(err.value, name="base_url", host="other.example.internal")
    assert http.checked_origins == before


def test_an_adapter_factory_refuses_a_url_with_userinfo() -> None:
    cfg = _llm_cfg()
    spec = ADAPTERS["langgraph"]
    module = importlib.import_module(spec.module, package="donkey_kit.integrations")
    adapter = getattr(module, spec.cls)(cfg, DonkeyAsyncClient(cfg, None))

    with pytest.raises(ConfigError) as err:
        adapter.chat_model("m", base_url=_with_userinfo("other.example.internal", path="/v1/"))

    _assert_userinfo_refusal(err.value, name="base_url", host="other.example.internal")
