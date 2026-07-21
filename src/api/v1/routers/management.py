"""관리용 API 라우터.

카메라 연결 상태를 점검하는 health check 엔드포인트를 제공한다.
"""

from typing import Literal
from fastapi import APIRouter, Request
from pydantic import BaseModel

from utils.device import iter_capture_device_serials

router = APIRouter(tags=["management"])


class CameraInfo(BaseModel):
    """카메라 식별 정보 (serial + 논리 index)."""

    serial: str
    index: int


class HealthResponse(BaseModel):
    """health check 응답. UNHEALTHY이면 누락된 카메라 목록을 함께 담는다."""

    status: Literal["HEALTHY", "UNHEALTHY"]
    missing_cameras: list[CameraInfo] | None = None

    model_config = {
        "json_schema_extra": {
            "examples": [
                {"status": "HEALTHY"},
                {
                    "status": "UNHEALTHY",
                    "missing_cameras": [
                        {"serial": "1234567890", "index": 1},
                        {"serial": "0987654321", "index": 2},
                    ],
                },
            ]
        }
    }


@router.get("/health", description="""
            Check the health status of connected cameras. <br />
            Returns "HEALTHY" if all cameras are connected, <br />
            otherwise returns "UNHEALTHY" with a list of missing cameras. <br />
            <br />
            Examples: <br />
            <ul>
                <li>HEALTHY: <code>{"status": "HEALTHY"}</code></li>
                <li>UNHEALTHY: <code>{"status": "UNHEALTHY", "missing_cameras": [{"serial": "1234567890", "index": 1}]}</code></li>
            </ul>
            """, response_model=HealthResponse)
async def get_health(request: Request) -> HealthResponse:
    """mapping.json의 모든 카메라가 실제로 연결되어 있는지 점검한다."""
    serials = set(iter_capture_device_serials(request.app.state.pyudev_context))
    missing_cameras = []
    for obj in request.app.state.camera_mapping:
        device = obj["device"]
        serial = device["serial"]
        if serial not in serials:
            # index는 애플리케이션 전반에서 카메라를 식별하는 논리 index(mapping.index)
            missing_cameras.append(CameraInfo(serial=serial, index=obj["mapping"]["index"]))
    if missing_cameras:
        return HealthResponse(status="UNHEALTHY", missing_cameras=missing_cameras)
    else:
        return HealthResponse(status="HEALTHY")
