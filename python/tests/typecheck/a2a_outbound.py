"""Downstream outbound A2A signatures (#1395), checked by mypy, not pytest."""

from __future__ import annotations

from collections.abc import AsyncIterator

from a2a.types import SendMessageRequest, SendMessageResponse, StreamResponse

from donkey_kit import A2AFacade, Donkey, McpAuth
from donkey_kit.experimental import AssetRef, GovernanceCriteria


async def outbound(
    donkey: Donkey, ref: AssetRef, message: SendMessageRequest, auth: McpAuth
) -> SendMessageResponse:
    facade: A2AFacade = donkey.a2a
    response: SendMessageResponse = await facade.send(
        ref, message, auth=auth, criteria=GovernanceCriteria()
    )
    async with facade.stream(ref, message, auth=auth) as stream:
        events: AsyncIterator[StreamResponse] = stream
        async for event in events:
            streamed: StreamResponse = event
            assert streamed is event
    return response
