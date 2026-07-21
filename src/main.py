"""애플리케이션 진입점.

FastAPI 앱을 구성하고 lifespan 동안 카메라 캡처/저장 관련 서비스를
생성·기동·종료한다. 서비스 구성은 다음과 같다.

- CaptureService: mapping.json에 정의된 카메라별 frame 캡처 (카메라당 1개)
- SaveService: 전체 세션 아카이브 녹화 (top 카메라 전용)
- TriggerSaveService: loadcell trigger 기반 구간 녹화 (zone 1~5, zone당 1개;
  top/side 카메라 배치는 mapping.json의 role 정의를 따름 — utils/mapping.py)
- LoadcellService: IO-BOARD의 SSE 스트림을 구독해 trigger 발화 및 모델 서버 전송
- SamplerService: frame 단위 JPEG 샘플링 (데이터 수집용)

실행: `uv run src/main.py` (0.0.0.0:8003)
"""

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
from utils.mapping import parse_camera_layout
from utils.misc import read_json_file

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """앱 수명 주기 동안 서비스들을 생성/시작하고, 종료 시 역순으로 정리한다."""
    context = pyudev.Context()
    app.state.pyudev_context = context

    # mapping.json: 카메라 serial ↔ 논리 index 매핑 + top/side 배치(role)
    mapping = read_json_file("mapping.json")
    app.state.camera_mapping = mapping

    # 배치 해석: top 1대 + zone(1~5)별 side 카메라.
    # 냉동고는 side 1대가 전 zone을 공유, 냉장고는 zone마다 side 1대(총 6대).
    layout = parse_camera_layout(mapping)
    app.state.camera_layout = layout

    camera_control = CameraControl(
        width=640,
        height=480,
        format="MJPG",
        fps=30,
        extra={
            "power_line_frequency": 0,
        },
    )

    # 논리 index별 CaptureService 생성 및 시작
    capture_services: dict[int, CaptureService] = {}
    for entry in mapping:
        capture_service = CaptureService(context, entry["device"]["serial"], entry["device"]["index"], camera_control)
        await capture_service.start()
        capture_services[entry["mapping"]["index"]] = capture_service
    app.state.capture_services = capture_services

    # 배치가 참조하는 카메라가 실제로 mapping에 존재하는지 기동 시점에 확인
    referenced = {layout.top_index, *layout.zone_side_indices.values()}
    missing = sorted(referenced - set(capture_services))
    if missing:
        raise ValueError(f"mapping.json의 배치가 존재하지 않는 카메라 index를 참조합니다: {missing}")

    # 아카이브용 SaveService는 top 카메라에만 붙인다.
    save_services: dict[int, SaveService] = {
        layout.top_index: SaveService(capture_services[layout.top_index], name="")
    }
    app.state.save_services = save_services

    # zone 1~5 각각에 TriggerSaveService를 둔다. top은 전 zone 공유,
    # side는 layout(zone → side 카메라)을 따른다.
    trigger_save_services: dict[int, TriggerSaveService] = {}
    for zone, side_index in layout.zone_side_indices.items():
        trigger_save_service = TriggerSaveService(
            {
                "top": capture_services[layout.top_index],
                "side": capture_services[side_index],
            }
        )
        await trigger_save_service.start()
        trigger_save_services[zone] = trigger_save_service
    app.state.trigger_save_services = trigger_save_services

    loadcell_service = LoadcellService(
        # filter_method=none: IO-BOARD가 sanitizer+5g 양자화를 거친 값을 주므로
        # EMA가 불필요하고, 0.8s 폴링에서는 EMA(α=0.8) 정착 꼬리가 벽시계 ~2.4s로
        # 늘어나 모델의 plateau 성립(연속 3샘플 std≤2.5)을 포스트롤 밖으로 밀어냄
        # (CRK-model-HG issue #12: delta=0). 모델은 filtered_value를 우선 쓰기
        # 때문에 여기 필터 선택이 곧 모델 입력이다. 양자화 경계 토글(delta=5.0)은
        # threshold(>5)를 못 넘으므로 change 발화에도 안전.
        sse_url="http://localhost:8000/sse?streams=loadcells&filter_method=none&threshold=5",
        trigger_save_services=trigger_save_services,
    )
    app.state.loadcell_service = loadcell_service

    sampling_service = SamplerService(capture_services=capture_services)
    app.state.sampling_service = sampling_service

    app.state.events = {}

    yield

    # 종료: 의존 방향의 역순(sampling → trigger save → loadcell → save → capture)으로 정리
    # (except Exception: CancelledError는 삼키지 않고 전파해 강제 종료를 막지 않는다)
    try:
        await app.state.sampling_service.stop()
    except Exception:
        logger.exception("Error stopping sampling service")

    for service in app.state.trigger_save_services.values():
        try:
            await service.stop()
        except Exception:
            logger.exception("Error stopping trigger save service")

    try:
        await app.state.loadcell_service.stop()
    except Exception:
        logger.exception("Error stopping loadcell service")

    for service in app.state.save_services.values():
        try:
            await service.stop()
        except Exception:
            logger.exception("Error stopping save service")

    for service in app.state.capture_services.values():
        try:
            await service.stop()
        except Exception:
            logger.exception("Error stopping capture service")


app = FastAPI(lifespan=lifespan)

app.include_router(management.router)
app.include_router(recording.router)
app.include_router(sampling.router)

app.include_router(test.router)

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8003)
