import asyncio
import bisect
import json
import logging
from typing import Optional, Sequence

import httpx
from aiosseclient import Event, aiosseclient
from dateutil.parser import isoparse

from services.trigger_save import TriggerEvent, TriggerSaveService

logger = logging.getLogger(__name__)


class LoadcellService:
    def __init__(
        self,
        sse_url: str,
        trigger_save_services: dict[int, tuple[TriggerSaveService, TriggerSaveService]],
    ):
        self.sse_url = sse_url
        self.trigger_save_services = trigger_save_services

        self._loadcell_task = None
        self._loadcell_history = []
        self._event_tasks = []

    async def start(self):
        pass

    async def stop(self):
        pass

    async def _run(self):
        async for event in aiosseclient(self.sse_url):
            if event.event == "loadcell.update":
                await self._handle_loadcell_update(event)
            elif event.event == "loadcell.change":
                await self._handle_loadcell_change(event)

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
                events = (
                    await trigger_save_service[0].trigger(5.0),
                    await trigger_save_service[1].trigger(5.0),
                )
                if not any(events):
                    continue
                if events[0] is not None or events[1] is not None:
                    # THIS MAY CAUSE RACE CONDITION IF TWO LOADCELLS TRIGGER
                    logger.error("RACE CONDITION DETECTED IN LOADCELL SERVICE")
                    continue
                assert events[0] is not None and events[1] is not None
                event_task = asyncio.create_task(
                    self._wait_events_and_submit(
                        events, timestamp=data["timestamp_float"], zone=zone
                    )
                )
                self._event_tasks.append(event_task)

    async def _wait_events_and_submit(
        self, events: Sequence[Optional[TriggerEvent]], timestamp: float, zone: int
    ):
        for event in events:
            if event is not None:
                await event.event.wait()

        loadcells_index = (
            bisect.bisect_right(
                self._loadcell_history,
                timestamp - 1,
                key=lambda x: x["timestamp_float"],
            )
            - 1
        )

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

        assert events[0] is not None and events[1] is not None

        top_path = events[0].path
        side_path = events[1].path

        # send these data to server
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.post(
                    "http://localhost:8002/trigger",
                    json={
                        "zone": zone,
                        "loadcells": loadcells_data,
                        "videos": {
                            "top": top_path.as_posix(),
                            "side": side_path.as_posix(),
                        },
                    },
                )
                response.raise_for_status()
                logger.info(
                    f"Successfully submitted loadcell event for zone {zone} at {timestamp}"
                )
            except httpx.HTTPError as e:
                logger.error(f"Failed to submit loadcell event: {e}")