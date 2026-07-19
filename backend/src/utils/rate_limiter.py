import asyncio
from types import TracebackType
from typing import Optional, Type


class RateLimiter:
    def __init__(self, concurrent: int = 2, delay: float = 1.0):
        self._semaphore = asyncio.Semaphore(concurrent)
        self._delay = delay

    async def acquire(self) -> None:
        await self._semaphore.acquire()
        try:
            await asyncio.sleep(self._delay)
        except asyncio.CancelledError:
            self._semaphore.release()
            raise

    def release(self) -> None:
        self._semaphore.release()

    async def __aenter__(self) -> "RateLimiter":
        await self.acquire()
        return self

    async def __aexit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc_val: Optional[BaseException],
        exc_tb: Optional[TracebackType],
    ) -> None:
        self.release()
