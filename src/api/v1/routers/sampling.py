from fastapi import APIRouter, Request
from pydantic import BaseModel

from services.sampler import SamplerService

router = APIRouter(prefix="/sampling", tags=["sampling"])


class SamplingStartRequest(BaseModel):
    save_path: str
    cameras: list[int] = [0, 1]


@router.post("/start")
async def start_recording(request: Request, body: SamplingStartRequest):
    sampling_service: SamplerService = request.app.state.sampling_service
    await sampling_service.start(body.save_path, body.cameras)

    return {"status": "recording started"}


@router.post("/stop")
async def stop_recording(request: Request):
    sampling_service: SamplerService = request.app.state.sampling_service
    await sampling_service.stop()

    return {"status": "recording stopped"}