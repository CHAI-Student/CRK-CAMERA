"""녹화 제어 API 라우터.

/recording/start · /recording/stop 으로 한 세션의 녹화 전체
(아카이브 SaveService, zone별 TriggerSaveService session, loadcell SSE 구독)를
일괄 시작/종료한다. 저장 구조:

- <save_path>/archival/cam_<index>/ : 연속 아카이브 녹화 (MP4)
- <save_path>/inference/zone_<zone>/ : trigger 구간 녹화 (AVI)
"""

from fastapi import APIRouter, Request
from pydantic import BaseModel

from services.loadcell import LoadcellService
from services.save import SaveService
from services.trigger_save import TriggerSaveService

router = APIRouter(prefix="/recording", tags=["recording"])


class RecordingStartRequest(BaseModel):
    """녹화 시작 요청. save_path 아래에 세션 데이터가 저장된다."""

    save_path: str


@router.post("/start")
async def start_recording(request: Request, body: RecordingStartRequest):
    """아카이브 녹화, trigger session, loadcell 구독을 순서대로 시작한다."""
    save_services: dict[int, SaveService] = request.app.state.save_services
    for key, service in save_services.items():
        await service.start(f"{body.save_path}/archival/cam_{key}")

    trigger_save_services: dict[int, TriggerSaveService] = (
        request.app.state.trigger_save_services
    )
    for key, service in trigger_save_services.items():
        await service.start_session(f"{body.save_path}/inference/zone_{key}")
    
    loadcell_service: LoadcellService = request.app.state.loadcell_service
    await loadcell_service.start()

    return {"status": "recording started"}


@router.post("/stop")
async def stop_recording(request: Request):
    """trigger session, loadcell 구독, 아카이브 녹화를 순서대로 종료한다."""
    trigger_save_services: dict[int, TriggerSaveService] = (
        request.app.state.trigger_save_services
    )
    for service in trigger_save_services.values():
        await service.stop_session()

    loadcell_service: LoadcellService = request.app.state.loadcell_service
    await loadcell_service.stop()

    save_services: dict[int, SaveService] = request.app.state.save_services
    for service in save_services.values():
        await service.stop()

    return {"status": "recording stopped"}
