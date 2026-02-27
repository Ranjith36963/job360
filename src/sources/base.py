import asyncio
import logging
from abc import ABC, abstractmethod

import aiohttp

from src.models import Job
from src.config.settings import MAX_RETRIES, RETRY_BACKOFF, REQUEST_TIMEOUT

logger = logging.getLogger("job360.sources")


class BaseJobSource(ABC):
    name: str = "base"

    def __init__(self, session: aiohttp.ClientSession):
        self._session = session

    @abstractmethod
    async def fetch_jobs(self) -> list[Job]:
        ...

    async def _get_json(self, url: str, params: dict | None = None,
                        headers: dict | None = None) -> dict | list | None:
        for attempt in range(MAX_RETRIES):
            try:
                async with self._session.get(
                    url,
                    params=params,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
                ) as resp:
                    if resp.status in (404, 403, 401):
                        logger.debug(f"[{self.name}] HTTP {resp.status} from {url}")
                        return None
                    if resp.status >= 400:
                        logger.warning(f"[{self.name}] HTTP {resp.status} from {url}")
                        if attempt < MAX_RETRIES - 1:
                            await asyncio.sleep(RETRY_BACKOFF[attempt])
                            continue
                        return None
                    return await resp.json(content_type=None)
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                logger.warning(f"[{self.name}] Request error: {e}")
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(RETRY_BACKOFF[attempt])
        return None

    async def _post_json(self, url: str, body: dict | None = None,
                         headers: dict | None = None) -> dict | list | None:
        for attempt in range(MAX_RETRIES):
            try:
                async with self._session.post(
                    url,
                    json=body or {},
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
                ) as resp:
                    if resp.status in (404, 403, 401):
                        logger.debug(f"[{self.name}] HTTP {resp.status} from {url}")
                        return None
                    if resp.status >= 400:
                        logger.warning(f"[{self.name}] HTTP {resp.status} from {url}")
                        if attempt < MAX_RETRIES - 1:
                            await asyncio.sleep(RETRY_BACKOFF[attempt])
                            continue
                        return None
                    return await resp.json(content_type=None)
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                logger.warning(f"[{self.name}] Request error: {e}")
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(RETRY_BACKOFF[attempt])
        return None

    async def _get_text(self, url: str, params: dict | None = None,
                        headers: dict | None = None) -> str | None:
        for attempt in range(MAX_RETRIES):
            try:
                async with self._session.get(
                    url,
                    params=params,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
                ) as resp:
                    if resp.status >= 400:
                        if attempt < MAX_RETRIES - 1:
                            await asyncio.sleep(RETRY_BACKOFF[attempt])
                            continue
                        return None
                    return await resp.text()
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                logger.warning(f"[{self.name}] Request error: {e}")
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(RETRY_BACKOFF[attempt])
        return None
