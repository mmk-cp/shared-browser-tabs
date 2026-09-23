"""Bounded input queue that discards obsolete hover positions, not actions."""
import asyncio
from collections import deque


class InputBuffer:
    def __init__(self, maxsize: int = 128):
        self._events = deque()
        self._condition = asyncio.Condition()
        self._maxsize = maxsize

    @staticmethod
    def _merge(previous: dict, current: dict) -> dict | None:
        # Request IDs are acknowledgements: never silently remove one.
        if previous.get('id') is not None or current.get('id') is not None:
            return None
        if previous.get('type') != current.get('type'):
            return None
        if current.get('type') == 'move' and not previous.get('buttons') and not current.get('buttons'):
            return current
        # Keep scroll distance and ordering, but avoid a CDP round-trip per
        # wheel tick. Don't combine direction reversals or different targets.
        if current.get('type') == 'wheel' and all(previous.get(k) == current.get(k) for k in ('x', 'y')):
            try:
                values = {}
                for axis in ('dx', 'dy'):
                    before, after = float(previous.get(axis, 0)), float(current.get(axis, 0))
                    if before * after < 0 or abs(before + after) > 2000:
                        return None
                    values[axis] = before + after
                return {**current, **values}
            except (TypeError, ValueError):
                return None
        return None

    async def put(self, event: dict):
        async with self._condition:
            while True:
                if self._events:
                    merged = self._merge(self._events[-1], event)
                    if merged is not None:
                        self._events[-1] = merged
                        return
                if len(self._events) < self._maxsize:
                    self._events.append(event)
                    self._condition.notify_all()
                    return
                await self._condition.wait()

    async def get(self) -> dict:
        async with self._condition:
            await self._condition.wait_for(lambda: bool(self._events))
            event = self._events.popleft()
            self._condition.notify_all()
            return event
