"""The project the action's CI self-test publishes in a dry run (#219)."""

from __future__ import annotations

from donkey_kit import Donkey

donkey = Donkey.from_env()


@donkey.tool(server="sku-manager")
def lookup_sku(sku: str) -> dict:
    """Look up a SKU's price and stock level by its code."""
    return {"sku": sku}
