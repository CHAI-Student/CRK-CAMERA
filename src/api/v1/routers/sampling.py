"""frame 샘플링 제어 API 라우터.

/sampling/start · /sampling/stop 으로 SamplerService(JPEG 샘플링)를
시작/종료한다.
"""

from fastapi import APIRouter, Request
from pydantic import BaseModel

from services.sampler import SamplerService

router = APIRouter(prefix="/sampling", tags=["sampling"])


class SamplingStartRequest(BaseModel):
    """샘플링 시작 요청 (저장 경로와 대상 카메라 index 목록)."""

    save_path: str
    cameras: list[int] = [0, 1]


@router.post("/start")
async def start_recording(request: Request, body: SamplingStartRequest):
    """지정한 카메라들의 frame 샘플링을 시작한다."""
    sampling_service: SamplerService = request.app.state.sampling_service
    await sampling_service.start(body.save_path, body.cameras)

    return {"status": "recording started"}


@router.post("/stop")
async def stop_recording(request: Request):
    """frame 샘플링을 종료한다."""
    sampling_service: SamplerService = request.app.state.sampling_service
    await sampling_service.stop()

    return {"status": "recording stopped"}