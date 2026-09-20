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

from typing import Any, Literal

from pydantic import BaseModel, Field


class ToolSpec(BaseModel):
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
    assetId: str
    version: str
    upstream: str
    tools: list[ToolSpec] = Field(default_factory=list)
    httpMapping: HttpMapping | None = None


class PolicySpec(BaseModel):
    assetId: str
    version: str
    config: dict[str, Any] = Field(default_factory=dict)


class McpBridgeSpec(BaseModel):
    name: str
    gateway: str
    apis: list[ApiSpec] = Field(default_factory=list)
    policies: list[PolicySpec] = Field(default_factory=list)


class SpecMetadata(BaseModel):
    name: str
    environment: str
    businessGroup: str | None = None


class DonkeySpec(BaseModel):
    apiVersion: Literal["donkey/v1"] = "donkey/v1"
    kind: Literal["DonkeySpec"] = "DonkeySpec"
    metadata: SpecMetadata
    mcpBridges: list[McpBridgeSpec] = Field(default_factory=list)

    @classmethod
    def from_yaml(cls, text: str) -> DonkeySpec:
        import yaml  # part of the [cli] extra

        data = yaml.safe_load(text)
        return cls.model_validate(data)
