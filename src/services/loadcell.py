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
    ):
        self.sse_url = sse_url
        self.trigger_save_services = trigger_save_services

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

        # Cancel all pending event tasks
        for task in self._event_tasks:
            if not task.done():
                task.cancel()

        # Wait for all tasks to complete cancellation
        if self._event_tasks:
            await asyncio.gather(*self._event_tasks, return_exceptions=True)

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
                trigger_event = await trigger_save_service.trigger(3.0)
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
