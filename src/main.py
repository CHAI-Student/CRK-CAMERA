from contextlib import asynccontextmanager

import pyudev
from fastapi import FastAPI

from api.v1.routers import management, recording
from services.capture import CaptureService
from services.save import SaveService
from services.trigger_save import TriggerSaveService
from utils.misc import read_json_file


@asynccontextmanager
async def lifespan(app: FastAPI):
    context = pyudev.Context()
    app.state.pyudev_context = context

    mapping = read_json_file("mapping.json")
    app.state.camera_mapping = mapping

    capture_services: dict[int, CaptureService] = {}
    for key, value in mapping.items():
        capture_service = CaptureService(context, key)
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
    
    trigger_save_services: dict[int, tuple[TriggerSaveService, TriggerSaveService]] = {}
    for key, value in capture_services.items():
        if key == 0:
            continue
        trigger_save_service_top_camera = TriggerSaveService(capture_services[0], name="/top")
        trigger_save_service_side_camera = TriggerSaveService(value, name="/side")
        trigger_save_services[key] = (trigger_save_service_top_camera, trigger_save_service_side_camera)
    app.state.trigger_save_services = trigger_save_services
    
    app.state.events = {}

    yield

    for top_service, side_service in app.state.trigger_save_services.values():
        await top_service.stop()
        await side_service.stop()

    for service in app.state.capture_services.values():
        await service.stop()


app = FastAPI(lifespan=lifespan)

app.include_router(management.router)
app.include_router(recording.router)

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8003)
