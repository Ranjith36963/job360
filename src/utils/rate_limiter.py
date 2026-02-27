import asyncio


class RateLimiter:
    def __init__(self, concurrent: int = 2, delay: float = 1.0):
        self._semaphore = asyncio.Semaphore(concurrent)
        self._delay = delay

    async def acquire(self):
        await self._semaphore.acquire()
        await asyncio.sleep(self._delay)

    def release(self):
        self._semaphore.release()

    async def __aenter__(self):
        await self.acquire()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self.release()
