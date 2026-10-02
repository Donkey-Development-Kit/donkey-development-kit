"""Declarative spec models (provisioning-as-code).

``Governance.export()`` and ``Publication.export()`` emit fragments of exactly
this format — the features share ONE schema, deliberately. Do not let them
diverge (provisioning-as-code).

Design boundaries baked in here:
  * ``inputSchema: "auto"`` — derive from the API's published spec in Exchange
    (provisioning-as-code). The OAS/RAML→JSON-Schema transform runs offline in CI (planner).
  * DataWeave is a HARD boundary (provisioning-as-code): a raw ``httpMapping.dataweave`` string
    passes through untouched; we never generate or parse DataWeave.
  * No secrets in the spec — reference them (``${secret:...}``), resolved at
    apply time (provisioning-as-code).
"""

from __future__ import annotations

import warnings
from typing import Any, Literal

from pydantic import BaseModel, Field

__all__ = [
    "ApiSpec",
    "ApiToolSpec",
    "DonkeySpec",
    "HttpMapping",
    "McpBridgeSpec",
    "PolicySpec",
    "SpecMetadata",
]


class ApiToolSpec(BaseModel):
    """One MCP tool an :class:`ApiSpec` exposes: an API operation, by method and
    resource. Named ``ToolSpec`` before #719; it is a different type from the
    top-level :class:`donkey_kit.ToolSpec` (a ``@Donkey.tool`` registration)."""

    name: str
    method: str
    resource: str
    description: str
    #: "auto" derives JSON Schema from the OAS/RAML spec in Exchange (provisioning-as-code),
    #: or an explicit JSON Schema object.
    inputSchema: Literal["auto"] | dict[str, Any] = "auto"


class HttpMapping(BaseModel):
    """DataWeave passthrough only — never generated or parsed (provisioning-as-code)."""

    dataweave: str | None = None


class ApiSpec(BaseModel):
    """One Exchange API a bridge fronts, with the tools derived from it."""

    assetId: str
    version: str
    upstream: str
    tools: list[ApiToolSpec] = Field(default_factory=list)
    httpMapping: HttpMapping | None = None


class PolicySpec(BaseModel):
    """One policy applied to a bridge: its Exchange asset, version and config."""

    assetId: str
    version: str
    config: dict[str, Any] = Field(default_factory=dict)


class McpBridgeSpec(BaseModel):
    """One MCP bridge: the gateway it runs on, its APIs and its policies."""

    name: str
    gateway: str
    apis: list[ApiSpec] = Field(default_factory=list)
    policies: list[PolicySpec] = Field(default_factory=list)


class SpecMetadata(BaseModel):
    """The spec's name, target environment and optional business group."""

    name: str
    environment: str
    businessGroup: str | None = None


class DonkeySpec(BaseModel):
    """The root of a ``donkey.yaml`` provisioning spec (``apiVersion: donkey/v1``)."""

    apiVersion: Literal["donkey/v1"] = "donkey/v1"
    kind: Literal["DonkeySpec"] = "DonkeySpec"
    metadata: SpecMetadata
    mcpBridges: list[McpBridgeSpec] = Field(default_factory=list)

    @classmethod
    def from_yaml(cls, text: str) -> DonkeySpec:
        """Parse and validate a ``donkey.yaml`` document.

        Raises:
            pydantic.ValidationError: The document does not match the spec schema.
        """
        import yaml  # part of the [cli] extra

        data = yaml.safe_load(text)
        return cls.model_validate(data)


def __getattr__(name: str) -> type[ApiToolSpec]:
    # Deprecated alias (#719): ``ToolSpec`` collided with ``donkey_kit.ToolSpec``.
    if name == "ToolSpec":
        warnings.warn(
            "donkey_kit.provisioning.spec.ToolSpec is deprecated; use ApiToolSpec.",
            DeprecationWarning,
            stacklevel=2,
        )
        return ApiToolSpec
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
