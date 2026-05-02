"""Async fan-out from the inference thread to WebSocket clients."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

log = logging.getLogger("counter.broadcaster")


class Broadcaster:
    """Holds per-client asyncio queues. Thread-safe enqueue from worker thread."""

    def __init__(self, queue_size: int = 256) -> None:
        self._queues: set[asyncio.Queue[dict[str, Any]]] = set()
        self._lock = asyncio.Lock()
        self._queue_size = queue_size
        self._loop: asyncio.AbstractEventLoop | None = None

    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    async def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=self._queue_size)
        async with self._lock:
            self._queues.add(queue)
        return queue

    async def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        async with self._lock:
            self._queues.discard(queue)

    def publish_threadsafe(self, message: dict[str, Any]) -> None:
        """Enqueue from a non-async thread (the inference worker)."""
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        try:
            loop.call_soon_threadsafe(self._publish_sync, message)
        except RuntimeError:
            # Loop is shutting down.
            pass

    def _publish_sync(self, message: dict[str, Any]) -> None:
        for queue in list(self._queues):
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                # Drop the oldest item, then enqueue the new one. Live counts
                # should always reflect latest reality even under backpressure.
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    queue.put_nowait(message)
                except asyncio.QueueFull:
                    pass
