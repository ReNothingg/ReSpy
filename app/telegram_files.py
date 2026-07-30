from __future__ import annotations

from io import BytesIO

import aiohttp


class BotApiFileDownloader:
    def __init__(self, bot_token: str) -> None:
        self._api_url = f"https://api.telegram.org/bot{bot_token}"
        self._file_url = f"https://api.telegram.org/file/bot{bot_token}"

    async def download(self, file_id: str, destination: BytesIO) -> None:
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(
                f"{self._api_url}/getFile",
                params={"file_id": file_id},
            ) as response:
                response.raise_for_status()
                payload = await response.json()
            file_path = payload.get("result", {}).get("file_path")
            if not payload.get("ok") or not file_path:
                raise RuntimeError("Telegram did not return a file path")
            async with session.get(f"{self._file_url}/{file_path}") as response:
                response.raise_for_status()
                destination.write(await response.read())
