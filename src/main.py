import logging
from contextlib import asynccontextmanager

import pyudev
from fastapi import FastAPI

from api.v1.routers import management, recording, sampling, test
from services.capture import CaptureService
from services.loadcell import LoadcellService
from services.sampler import SamplerService
from services.save import SaveService
from services.trigger_save import TriggerSaveService
from utils.camera import CameraControl
from utils.misc import read_json_file

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    context = pyudev.Context()
    app.state.pyudev_context = context

    mapping = read_json_file("mapping.json")
    app.state.camera_mapping = mapping

    camera_control = CameraControl(
        width=640,
        height=480,
        format="MJPG",
        fps=50,
        extra={
            "power_line_frequency": 0,
        },
    )

    capture_services: dict[int, CaptureService] = {}
    for key, value in mapping.items():
        capture_service = CaptureService(context, key, camera_control)
        await capture_service.start()
        capture_services[value] = capture_service
    app.state.capture_services = capture_services

    save_services: dict[int, SaveService] = {}
    for key, value in capture_services.items():
        if key != 0:
            continue
        save_service = SaveService(value, name="")
        save_services[key] = save_service
    app.state.save_services = save_services

    trigger_save_services: dict[int, TriggerSaveService] = {}
    for key, value in capture_services.items():
        if key == 0:
            continue
        trigger_save_service = TriggerSaveService(
            {
                "top": capture_services[0],
                "side": value,
            }
        )
        await trigger_save_service.start()
        trigger_save_services[key] = trigger_save_service
    app.state.trigger_save_services = trigger_save_services

    loadcell_service = LoadcellService(
        sse_url="http://localhost:8000/sse?streams=loadcells&filter_method=exponential&filter_alpha=0.8&threshold=2",
        trigger_save_services=trigger_save_services,
    )
    app.state.loadcell_service = loadcell_service

    sampling_service = SamplerService(capture_services=capture_services)
    app.state.sampling_service = sampling_service

    app.state.events = {}

    yield

    try:
        await app.state.sampling_service.stop()
    except:
        logger.exception(f"Error stopping sampling service")

    for service in app.state.trigger_save_services.values():
        try:
            await service.stop()
        except:
            logger.exception(f"Error stopping trigger save service")

    try:
        await app.state.loadcell_service.stop()
    except:
        logger.exception(f"Error stopping loadcell service")

    for service in app.state.save_services.values():
        try:
            await service.stop()
        except:
            logger.exception(f"Error stopping save service")

    for service in app.state.capture_services.values():
        try:
            await service.stop()
        except:
            logger.exception(f"Error stopping capture service")


app = FastAPI(lifespan=lifespan)

app.include_router(management.router)
app.include_router(recording.router)
app.include_router(sampling.router)

app.include_router(test.router)

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8003)
