import asyncio
import logging
from dataclasses import dataclass

import pyudev

from utils.camera import run_camera

logger = logging.getLogger(__name__)


@dataclass
class CaptureFrame:
    serial: str
    data: bytes
    timestamp: float
    frame_nb: int


class CaptureService:
    def __init__(
        self,
        context: pyudev.Context,
        serial: str,
        width: int = 640,
        height: int = 480,
        pixel_format: str = "YUYV",
        fps: int = 30,
    ):
        self.context = context
        self.serial = serial

        self.width = width
        self.height = height
        self.pixel_format = pixel_format
        self.fps = fps

        self._lock = asyncio.Lock()
        self._is_running = False

        self._subscribers_lock = asyncio.Lock()
        self._subscribers: set[asyncio.Queue[CaptureFrame]] = set()
        self._capture_task: asyncio.Task | None = None

    async def start(self):
        async with self._lock:
            if self._is_running:
                logger.warning(f"Capture for serial {self.serial} is already running")
                return
            self._is_running = True

        self._capture_task = asyncio.create_task(self._run_with_retries())

    async def stop(self):
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
        async with self._subscribers_lock:
            self._subscribers.add(queue)

    async def unsubscribe(self, queue: asyncio.Queue[CaptureFrame]):
        async with self._subscribers_lock:
            self._subscribers.discard(queue)

    async def _run_with_retries(self):
        assert self._capture_task is not None
        while self._is_running and not self._capture_task.cancelled():
            try:
                await self._run()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(f"Unexpected error in camera {self.serial}")
                if not self._is_running or self._capture_task.cancelled():
                    raise
                await asyncio.sleep(1)

    async def _run(self):
        camera = run_camera(
            self.context,
            self.serial,
            width=self.width,
            height=self.height,
            pixel_format=self.pixel_format,
            fps=self.fps,
        )

        try:
            async for frame in camera:
                # Skip empty frames
                if len(frame) == 0:
                    continue
                # Hold the subscribers lock briefly to get the current subscribers
                async with self._subscribers_lock:
                    # Discard frames if there are no subscribers
                    if not self._subscribers:
                        continue
                    # Create a snapshot of subscribers to avoid holding the lock while publishing
                    subscribers_snapshot = tuple(self._subscribers)
                # Prepare the item to send to subscribers
                frame_data = CaptureFrame(
                    self.serial,
                    frame.data[:],
                    frame.timestamp,
                    frame.frame_nb,
                )
                # Publish to all subscribers
                for subscriber in subscribers_snapshot:
                    try:
                        subscriber.put_nowait(frame_data)
                    except asyncio.QueueFull:
                        pass
                    except asyncio.QueueShutDown:
                        await self.unsubscribe(subscriber)
        finally:
            await camera.aclose()
