from __future__ import annotations

import asyncio
import os
import httpx
from shared.models import Job, RunnerInfo


class RunnerPoller:
    def __init__(self, runner_id: str, sre_id: str, telegram_id: str):
        self.runner_id = runner_id
        self.sre_id = sre_id
        self.telegram_id = telegram_id
        self.base_url = os.environ["MANAGER_URL"].rstrip("/")
        self._running = False

    async def register(self, client: httpx.AsyncClient) -> None:
        info = RunnerInfo(
            runner_id=self.runner_id,
            sre_id=self.sre_id,
            telegram_id=self.telegram_id,
            capabilities=["kubectl", "redis", "db"],
        )
        await client.post(
            f"{self.base_url}/api/runner/register",
            json=info.model_dump(),
            timeout=10,
        )
        print(f"[Runner] Registered as {self.runner_id}")

    async def _heartbeat_loop(self, client: httpx.AsyncClient) -> None:
        while self._running:
            try:
                await client.post(
                    f"{self.base_url}/api/runner/heartbeat/{self.runner_id}",
                    timeout=5,
                )
            except Exception:
                pass
            await asyncio.sleep(30)

    async def run(self, on_job) -> None:
        """Main loop: register → poll liên tục → gọi on_job khi có task."""
        self._running = True
        async with httpx.AsyncClient() as client:
            await self.register(client)
            asyncio.create_task(self._heartbeat_loop(client))

            print(f"[Runner] Polling {self.base_url} ...")
            while self._running:
                try:
                    resp = await client.post(
                        f"{self.base_url}/api/runner/poll/{self.runner_id}",
                        timeout=35,  # server timeout 30s + buffer
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        if data.get("job"):
                            job = Job(**data["job"])
                            asyncio.create_task(on_job(job))
                except httpx.TimeoutException:
                    pass  # Timeout bình thường → poll lại
                except Exception as e:
                    print(f"[Runner] Poll error: {e}")
                    await asyncio.sleep(5)

    def stop(self) -> None:
        self._running = False
