from fastapi import APIRouter, Request
from pydantic import BaseModel

from services.loadcell import LoadcellService
from services.save import SaveService
from services.trigger_save import TriggerSaveService

router = APIRouter(prefix="/recording", tags=["recording"])


class RecordingStartRequest(BaseModel):
    save_path: str


@router.post("/start")
async def start_recording(request: Request, body: RecordingStartRequest):
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
    trigger_save_services: dict[int, TriggerSaveService] = (
        request.app.state.trigger_save_services
    )
    for key, service in trigger_save_services.items():
        await service.stop_session()

    loadcell_service: LoadcellService = request.app.state.loadcell_service
    await loadcell_service.stop()

    save_services: dict[int, SaveService] = request.app.state.save_services
    for key, service in save_services.items():
        await service.stop()

    return {"status": "recording stopped"}
