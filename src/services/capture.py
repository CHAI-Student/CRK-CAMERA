"""카메라 frame 캡처 서비스.

serial로 지정된 V4L2 카메라에서 frame을 읽어, 구독(subscribe)한 모든
asyncio.Queue로 배포(pub-sub)한다. 캡처 도중 오류가 나면 1초 간격으로
자동 재시도하여 카메라 재연결 상황에도 서비스가 유지되게 한다.
"""

import asyncio
import logging
from dataclasses import dataclass

import pyudev

from utils.camera import CameraControl, run_camera

logger = logging.getLogger(__name__)


@dataclass
class CaptureFrame:
    """구독자에게 전달되는 frame 1장의 데이터.

    :param serial: frame을 생성한 카메라의 serial
    :param pixel_format: 픽셀 포맷 이름 (예: MJPEG, YUYV)
    :param data: frame 원본 바이트
    :param timestamp: 드라이버가 부여한 frame timestamp
    :param frame_nb: 드라이버가 부여한 frame 순번
    """

    serial: str
    pixel_format: str
    data: bytes
    timestamp: float
    frame_nb: int


class CaptureService:
    """카메라 1대의 frame을 캡처하여 구독자 queue들로 배포하는 서비스."""

    def __init__(
        self,
        context: pyudev.Context,
        serial: str,
        index: int,
        control: CameraControl = CameraControl(),
    ):
        self.context = context
        self.serial = serial
        self.index = index

        self.control = control

        self._lock = asyncio.Lock()
        self._is_running = False

        self._subscribers_lock = asyncio.Lock()
        self._subscribers: set[asyncio.Queue[CaptureFrame]] = set()
        self._capture_task: asyncio.Task | None = None

    async def start(self):
        """캡처 태스크를 시작한다. 이미 실행 중이면 경고만 남긴다."""
        async with self._lock:
            if self._is_running:
                logger.warning(f"Capture for serial {self.serial} is already running")
                return
            self._is_running = True

        self._capture_task = asyncio.create_task(self._run_with_retries())

    async def stop(self):
        """캡처 태스크를 취소하고 종료될 때까지 기다린다."""
        async with self._lock:
            if not self._is_running:
                logger.warning(f"Capture for serial {self.serial} is not running")
                return
            self._is_running = False

        assert self._capture_task is not None
        self._capture_task.cancel()
        try:
            await self._capture_task
        except asyncio.CancelledError:
            pass
        self._capture_task = None

    async def subscribe(self, queue: asyncio.Queue[CaptureFrame]):
        """frame을 받을 queue를 구독자로 등록한다."""
        async with self._subscribers_lock:
            self._subscribers.add(queue)

    async def unsubscribe(self, queue: asyncio.Queue[CaptureFrame]):
        """구독자 queue를 제거한다. 등록되어 있지 않아도 오류 없이 넘어간다."""
        async with self._subscribers_lock:
            self._subscribers.discard(queue)

    async def _run_with_retries(self):
        """_run을 감싸 예외 발생 시 최소 1초 간격으로 재시도한다."""
        assert self._capture_task is not None

        wait_time = 0

        while self._is_running and not self._capture_task.cancelled():
            try:
                await self._run()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(f"Unexpected error in camera {self.serial}")
                if not self._is_running or self._capture_task.cancelled():
                    raise

                loop = asyncio.get_running_loop()

                sleep_time = wait_time - loop.time()

                if sleep_time > 0.0:
                    await asyncio.sleep(sleep_time)

                wait_time = loop.time() + 1.0

    async def _run(self):
        """카메라에서 frame을 읽어 구독자 전원에게 배포하는 본체 루프."""
        camera = run_camera(
            self.context,
            self.serial,
            self.index,
            control=self.control,
        )

        try:
            async for frame in camera:
                # 빈 frame은 건너뛴다
                if len(frame) == 0:
                    continue
                # 구독자 목록 스냅샷만 lock 안에서 뜨고, 배포는 lock 밖에서 수행
                async with self._subscribers_lock:
                    # 구독자가 없으면 frame을 버린다
                    if not self._subscribers:
                        continue
                    subscribers_snapshot = tuple(self._subscribers)
                # 구독자에게 전달할 CaptureFrame 구성
                frame_data = CaptureFrame(
                    self.serial,
                    frame.pixel_format.name,
                    frame.data[:],
                    frame.timestamp,
                    frame.frame_nb,
                )
                # 모든 구독자에게 배포한다. 가득 찬 queue는 frame을 버리고,
                # shutdown된 queue는 구독을 해지한다.
                for subscriber in subscribers_snapshot:
                    try:
                        subscriber.put_nowait(frame_data)
                    except asyncio.QueueFull:
                        pass
                    except asyncio.QueueShutDown:
                        await self.unsubscribe(subscriber)
        finally:
            await camera.aclose()
