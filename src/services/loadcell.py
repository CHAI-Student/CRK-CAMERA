import asyncio
import bisect
import json
import logging
from typing import Optional

import httpx
from aiosseclient import Event, aiosseclient
from dateutil.parser import isoparse

from services.trigger_save import TriggerEvent, TriggerSaveService

logger = logging.getLogger(__name__)


class LoadcellService:
    def __init__(
        self,
        sse_url: str,
        trigger_save_services: dict[int, TriggerSaveService],
        submit_flush_timeout: float = 10.0,
    ):
        self.sse_url = sse_url
        self.trigger_save_services = trigger_save_services
        # stop() 시 대기 중인 submit(POST /trigger) 태스크에 주는 완료 유예.
        # stop 시점에는 stop_session이 모든 에피소드의 on_finish를 이미 set한
        # 상태라 정상적으로는 수백 ms 안에 끝난다 — 상한은 모델 서버가 행일
        # 때의 방어선일 뿐이다.
        self.submit_flush_timeout = submit_flush_timeout

        self._loadcell_task = None
        self._loadcell_history = []
        self._event_tasks = []

    async def start(self):
        if self._loadcell_task is not None:
            logger.warning("LoadcellService is already running")
            return
        self._loadcell_history.clear()
        self._loadcell_task = asyncio.create_task(self._run())
        logger.info("LoadcellService started")

    async def stop(self):
        if self._loadcell_task is None:
            logger.warning("LoadcellService is not running")
            return
        self._loadcell_task.cancel()
        try:
            await self._loadcell_task
        except asyncio.CancelledError:
            pass
        self._loadcell_task = None

        # Flush pending submit tasks instead of cancelling them outright.
        # An episode still recording when /recording/stop arrives has just
        # been force-closed by stop_session (its on_finish is set), so its
        # POST /trigger completes within ~100ms. Cancelling here used to
        # kill that POST — the recording directory existed (counted into
        # the edge watermark's expected_triggers) but its trigger never
        # reached the model, stalling door-close settlement until timeout.
        if self._event_tasks:
            done, pending = await asyncio.wait(
                self._event_tasks, timeout=self.submit_flush_timeout
            )
            if pending:
                logger.warning(
                    f"Cancelling {len(pending)} submit task(s) still pending "
                    f"after {self.submit_flush_timeout}s flush timeout"
                )
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)

        self._event_tasks.clear()
        logger.info("LoadcellService stopped")

    async def _run(self):
        try:
            async for event in aiosseclient(self.sse_url):
                if event.event == "loadcell.update":
                    await self._handle_loadcell_update(event)
                elif event.event == "loadcell.change":
                    await self._handle_loadcell_change(event)
        except asyncio.CancelledError:
            logger.info("LoadcellService._run cancelled")
            raise
        except Exception as e:
            logger.error(
                f"Unexpected error in LoadcellService._run: {e}", exc_info=True
            )

    async def _handle_loadcell_update(self, event: Event):
        data = json.loads(event.data)
        data["timestamp_float"] = isoparse(data["timestamp"]).timestamp()
        self._loadcell_history.append(data)

    async def _handle_loadcell_change(self, event: Event):
        data = json.loads(event.data)
        data["timestamp_float"] = isoparse(data["timestamp"]).timestamp()
        affected_zones = set()
        for changed_index in data["changed_indices"]:
            affected_zones.add(changed_index // 2 + 1)
        for zone in affected_zones:
            trigger_save_service = self.trigger_save_services.get(zone)
            if trigger_save_service:
                trigger_event = await trigger_save_service.trigger(
                    3.0, data["timestamp_float"]
                )
                # Both events must be present to proceed
                if trigger_event is None:
                    logger.info(
                        f"Zone {zone}: No trigger event returned (i.e. session already active; extending)"
                    )
                    continue

                trigger_event_task = asyncio.create_task(
                    self._wait_event_and_submit(
                        event=trigger_event,
                        timestamp=data["timestamp_float"],
                        zone=zone,
                    )
                )
                self._event_tasks.append(trigger_event_task)

    async def _wait_event_and_submit(
        self, event: Optional[TriggerEvent], timestamp: float, zone: int
    ):
        assert event is not None

        await event.event.wait()

        # Find the index of the first entry at or after (timestamp - 0.5)
        loadcells_index = bisect.bisect_left(
            self._loadcell_history,
            timestamp - 1,
            key=lambda x: x["timestamp_float"],
        )

        # Ensure we have valid history data
        if loadcells_index < 0:
            logger.warning(
                f"Zone {zone}: No loadcell history data found before timestamp {timestamp}"
            )
            return

        if loadcells_index >= len(self._loadcell_history):
            loadcells_index = len(self._loadcell_history) - 1

        zone_index = (zone - 1) * 2

        loadcells_data = [
            {
                "timestamp": entry["timestamp"],
                "raw_value": entry["raw_values"][zone_index : zone_index + 2],
                "filtered_value": entry["filtered_values"][zone_index : zone_index + 2],
                "filter_method": entry["filter_method"],
            }
            for entry in self._loadcell_history[loadcells_index:]
        ]

        # send these data to server
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.post(
                    "http://localhost:8002/trigger",
                    json={
                        "zone": zone,
                        "loadcells": loadcells_data,
                        # Wall-clock anchors of every change that started or
                        # extended this episode — lets the model reconstruct
                        # sub-events inside a merged episode. Optional field;
                        # older model versions ignore it.
                        "change_timestamps": list(event.change_timestamps),
                        "videos": {
                            "top": event.paths["top"].as_posix(),
                            "side": event.paths["side"].as_posix(),
                        },
                    },
                )
                response.raise_for_status()
                logger.info(
                    f"Successfully submitted loadcell event for zone {zone} at {timestamp}"
                )
            except httpx.HTTPError as e:
                logger.error(f"Failed to submit loadcell event: {e}")
