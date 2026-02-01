from typing import Literal
from fastapi import APIRouter, Request
from pydantic import BaseModel

from utils.device import iter_capture_device_serials

router = APIRouter(tags=["management"])


class CameraInfo(BaseModel):
    serial: str
    index: int


class HealthResponse(BaseModel):
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
    serials = set(iter_capture_device_serials(request.app.state.pyudev_context))
    missing_cameras = []
    for serial, index in request.app.state.camera_mapping.items():
        if serial not in serials:
            missing_cameras.append(CameraInfo(serial=serial, index=index))
    if missing_cameras:
        return HealthResponse(status="UNHEALTHY", missing_cameras=missing_cameras)
    else:
        return HealthResponse(status="HEALTHY")
