"""loadcell SSE 구독 및 trigger 발화 서비스.

IO-BOARD가 제공하는 SSE 스트림에서 loadcell 이벤트를 구독한다.

- loadcell.update: 주기 측정값 → history에 누적 (모델 전송용)
- loadcell.change: 무게 변화 감지 → 해당 zone의 TriggerSaveService를
  trigger해 구간 녹화를 시작/연장하고, 녹화가 끝나면 change 전후의
  loadcell history와 영상 경로를 모델 서버(POST /trigger)로 전송한다.
"""

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
    """loadcell SSE 이벤트를 받아 zone별 녹화 trigger와 모델 전송을 담당한다.

    :param sse_url: IO-BOARD loadcell SSE 스트림 URL
    :param trigger_save_services: zone 번호별 TriggerSaveService
    :param submit_flush_timeout: stop 시 대기 중인 submit 태스크 완료 유예 (초)
    """

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
        """SSE 구독 태스크를 시작하고 loadcell history를 초기화한다."""
        if self._loadcell_task is not None:
            logger.warning("LoadcellService is already running")
            return
        self._loadcell_history.clear()
        self._loadcell_task = asyncio.create_task(self._run())
        logger.info("LoadcellService started")

    async def stop(self):
        """SSE 구독을 중단하고, 대기 중인 submit 태스크를 flush한 뒤 종료한다."""
        if self._loadcell_task is None:
            logger.warning("LoadcellService is not running")
            return
        self._loadcell_task.cancel()
        try:
            await self._loadcell_task
        except asyncio.CancelledError:
            pass
        self._loadcell_task = None

        # 대기 중인 submit 태스크는 즉시 취소하지 않고 완료를 기다린다(flush).
        # /recording/stop 시점에 녹화 중이던 episode는 stop_session이 방금
        # 강제 종료해 on_finish가 set된 상태이므로 POST /trigger는 ~100ms 안에
        # 끝난다. 예전처럼 여기서 취소하면 그 POST가 죽어서 — 녹화 디렉터리는
        # 존재해 edge watermark의 expected_triggers에는 집계됐는데 trigger가
        # 모델에 도달하지 못해 — 문닫힘 정산이 timeout까지 지연됐다.
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
        """SSE 스트림을 소비하며 이벤트 종류별 핸들러를 호출한다."""
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
        """주기 측정값(loadcell.update)을 history에 누적한다."""
        data = json.loads(event.data)
        data["timestamp_float"] = isoparse(data["timestamp"]).timestamp()
        self._loadcell_history.append(data)

    async def _handle_loadcell_change(self, event: Event):
        """무게 변화(loadcell.change)에 대해 영향을 받은 zone들의 녹화를 trigger한다."""
        data = json.loads(event.data)
        data["timestamp_float"] = isoparse(data["timestamp"]).timestamp()
        # loadcell 채널은 zone당 2개: 채널 index // 2 + 1 = zone 번호
        affected_zones = set()
        for changed_index in data["changed_indices"]:
            affected_zones.add(changed_index // 2 + 1)
        for zone in affected_zones:
            trigger_save_service = self.trigger_save_services.get(zone)
            if trigger_save_service:
                # post-roll 4.0s: IO-BOARD의 0.8s loadcell 주기에서 모델은
                # 마지막 change 이후 안정 샘플 3개 이상(stable_window=3, 2.4s)이
                # 있어야 최종 plateau를 형성한다. 3.0s로는 여유가 없었다.
                trigger_event = await trigger_save_service.trigger(
                    4.0, data["timestamp_float"]
                )
                # None이면 이미 녹화 중(연장 처리됨)이거나 session이 닫힌 상태
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
        """녹화 완료(on_finish)를 기다린 뒤 loadcell history를 모델 서버로 전송한다."""
        assert event is not None

        await event.event.wait()

        # (timestamp - 4.0) 이후 첫 entry의 index를 찾는다.
        # 이 look-back은 모델의 change 이전 baseline 구간이다: 첫 plateau를
        # 형성하려면 안정 샘플 3개 이상(stable_window=3)이 필요하고, IO-BOARD의
        # 0.8s loadcell 주기에서는 2.4s + 전이 여유가 된다. 예전 1s look-back은
        # 과거 0.12s 주기에서는 8개 샘플을 담았지만 0.8s 주기에서는 1~2개뿐이라
        # baseline plateau가 형성되지 못해 delta_weight가 0으로 나왔다
        # (CRK-model-HG issue #12).
        loadcells_index = bisect.bisect_left(
            self._loadcell_history,
            timestamp - 4.0,
            key=lambda x: x["timestamp_float"],
        )

        # history 범위 보정
        if loadcells_index < 0:
            logger.warning(
                f"Zone {zone}: No loadcell history data found before timestamp {timestamp}"
            )
            return

        if loadcells_index >= len(self._loadcell_history):
            loadcells_index = len(self._loadcell_history) - 1

        # zone별 loadcell 채널 2개(zone_index, zone_index+1)만 잘라낸다
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

        # 모델 서버로 trigger payload 전송
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.post(
                    "http://localhost:8002/trigger",
                    json={
                        "zone": zone,
                        "loadcells": loadcells_data,
                        # 이 episode를 시작/연장한 모든 change의 wall-clock
                        # timestamp — 병합된 episode 내부의 하위 이벤트를
                        # 모델이 복원할 수 있게 한다. 선택 필드이며 구버전
                        # 모델은 무시한다.
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
