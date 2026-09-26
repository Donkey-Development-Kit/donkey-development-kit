"""Real Flex 1.14.0 Local Mode observations, verification ledger §6 / #65.

Opt-in Docker checks, separate from the shared adapter conformance scenarios.
No image pull, registration, control-plane mutation, or external traffic occurs.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

pytestmark = pytest.mark.local_gateway
IMAGE = (
    "mulesoft/flex-gateway@sha256:b21e1d901492bc2d2604848eff63e8c887c44a599f749d68212c5d19471452d1"
)
CONFIG = Path(__file__).parents[1] / "fixtures/local_gateway/config"


def docker(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", *args], capture_output=True, text=True, timeout=30, check=check
    )


@pytest.fixture(scope="module")
def flex_image() -> str:
    if shutil.which("docker") is None:
        pytest.skip("Docker is not installed")
    if docker("info", check=False).returncode:
        pytest.skip("Docker daemon is unavailable")
    image = os.environ.get("DDK_FLEX_GATEWAY_IMAGE", IMAGE)
    if docker("image", "inspect", image, check=False).returncode:
        pytest.skip(f"Local gateway image is absent: {image}; tests do not pull images")
    return image


@pytest.fixture
def gateway(request: pytest.FixtureRequest, flex_image: str, tmp_path: Path) -> Iterator[str]:
    registered = request.param
    if registered:
        registration = os.environ.get("DDK_FLEX_REGISTRATION")
        if not registration or not Path(registration).is_file():
            pytest.skip("Set DDK_FLEX_REGISTRATION to a Local Mode registration.yaml")
        shutil.copyfile(registration, tmp_path / "registration.yaml")
        (tmp_path / "registration.yaml").chmod(0o600)
        for config in CONFIG.glob("*.yaml"):
            shutil.copyfile(config, tmp_path / config.name)
    else:
        shutil.copyfile(CONFIG / "api.yaml", tmp_path / "api.yaml")
    name = f"ddk-local-probe-{uuid.uuid4().hex[:12]}"
    try:
        docker(
            "run",
            "--detach",
            "--platform",
            "linux/amd64",
            "--name",
            name,
            "--network",
            "none",
            "--mount",
            f"type=bind,source={tmp_path},target=/usr/local/share/mulesoft/"
            "flex-gateway/conf.d,readonly",
            flex_image,
        )
        yield name
    finally:
        docker("rm", "--force", name, check=False)
        (tmp_path / "registration.yaml").unlink(missing_ok=True)


def wait_for_log(name: str, expected: str) -> str:
    deadline = time.monotonic() + 60
    logs = ""
    while time.monotonic() < deadline:
        result = docker("logs", name)
        logs = result.stdout + result.stderr
        if expected in logs:
            return logs
        state = docker("inspect", "--format", "{{.State.Running}}", name)
        assert state.stdout.strip() == "true", logs
        time.sleep(0.5)
    pytest.fail(f"Gateway never logged {expected!r}:\n{logs}")


@pytest.mark.parametrize("gateway", [False], indirect=True)
def test_unregistered_gateway_rejects_configuration(gateway: str) -> None:
    wait_for_log(gateway, "the registration configuration is missing")
    result = docker("exec", gateway, "flexctl", "probe", "--check=readiness", check=False)
    assert result.returncode != 0


@pytest.mark.parametrize("gateway", [True], indirect=True)
def test_registered_stock_image_rejects_unavailable_extensions(gateway: str) -> None:
    logs = wait_for_log(gateway, "Extension default/mcp-support not found")
    assert "Extension default/llm-proxy-core not found" in logs
    assert "Extension default/model-based-routing not found" in logs
    # This proves rejection of these refs in this stock image, not that a custom
    # extension can never implement the capability. The ledger preserves that limit.


@pytest.mark.parametrize("gateway", [True], indirect=True)
def test_injection_protection_runs_in_registered_local_mode(gateway: str) -> None:
    wait_for_log(gateway, "Adding Policy default/ddk-injection-verification-p1")
    deadline = time.monotonic() + 60
    while docker(
        "exec", gateway, "nc", "-z", "-w", "1", "127.0.0.1", "8082", check=False
    ).returncode:
        if time.monotonic() >= deadline:
            pytest.fail("Gateway never opened the injection policy listener")
        time.sleep(0.5)
    body = b'{"input":"<script>alert(1)</script>"}'
    wire = (
        f"POST / HTTP/1.1\r\nHost: localhost\r\nContent-Type: application/json\r\n"
        f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n"
    ).encode() + body
    # BusyBox nc can exit before reading a reply if stdin closes immediately.
    # Keep stdin open; its network timeout bounds the response read.
    with subprocess.Popen(
        ["docker", "exec", "-i", gateway, "nc", "-w", "5", "127.0.0.1", "8082"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ) as proc:
        assert proc.stdin is not None and proc.stdout is not None
        proc.stdin.write(wire)
        proc.stdin.flush()
        response = proc.stdout.read()
        proc.wait(timeout=15)
        assert proc.returncode == 0
    headers, payload = response.split(b"\r\n\r\n", 1)
    assert headers.startswith(b"HTTP/1.1 400 ")
    assert b"x-injection-protection: blocked" in headers.lower()
    assert b"Injection attack detected" in payload
