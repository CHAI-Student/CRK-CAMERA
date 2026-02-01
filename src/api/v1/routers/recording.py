from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

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

    trigger_save_services: dict[int, tuple[TriggerSaveService, TriggerSaveService]] = (
        request.app.state.trigger_save_services
    )
    for key, (top_service, side_service) in trigger_save_services.items():
        await top_service.start(f"{body.save_path}/inference/zone_{key}")
        await side_service.start(f"{body.save_path}/inference/zone_{key}")
    
    loadcell_service: LoadcellService = request.app.state.loadcell_service
    await loadcell_service.start()

    return {"status": "recording started"}


@router.post("/stop")
async def stop_recording(request: Request):
    trigger_save_services: dict[int, tuple[TriggerSaveService, TriggerSaveService]] = (
        request.app.state.trigger_save_services
    )
    for key, (top_service, side_service) in trigger_save_services.items():
        await top_service.stop()
        await side_service.stop()

    loadcell_service: LoadcellService = request.app.state.loadcell_service
    await loadcell_service.stop()

    save_services: dict[int, SaveService] = request.app.state.save_services
    for key, service in save_services.items():
        await service.stop()

    return {"status": "recording stopped"}


class RecordingTriggerRequest(BaseModel):
    loadcell_idx: int = Field(..., ge=0, lt=10)
    duration: float = Field(..., gt=0)


@router.post("/trigger")
async def trigger_recording(request: Request, body: RecordingTriggerRequest):
    trigger_save_services: dict[int, tuple[TriggerSaveService, TriggerSaveService]] = (
        request.app.state.trigger_save_services
    )

    top_service, side_service = trigger_save_services[body.loadcell_idx // 2 + 1]

    top_service_event = await top_service.trigger(body.duration)
    side_service_event = await side_service.trigger(body.duration)

    # create a task to wait for the event to complete

    return {"status": "recording triggered"}
