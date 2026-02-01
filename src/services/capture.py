import asyncio
import logging
from dataclasses import dataclass
from typing import Optional

import pyudev

from utils.camera import run_camera

logger = logging.getLogger(__name__)


@dataclass
class CaptureFrame:
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

        self._subscribers: set[asyncio.Queue] = set()
        self._subscribers_lock = asyncio.Lock()
        self._capture_task: Optional[asyncio.Task] = None

    async def start(self):
        if self._capture_task is not None:
            logger.warning(f"Capture for serial {self.serial} is already running")
            return

        self._capture_task = asyncio.create_task(self._run_with_retries())

    async def stop(self):
        if self._capture_task is None:
            logger.warning(f"No capture task found for serial {self.serial}")
            return

        self._capture_task.cancel()
        try:
            await self._capture_task
        except:
            pass
        self._capture_task = None

    async def subscribe(self, queue: asyncio.Queue):
        async with self._subscribers_lock:
            self._subscribers.add(queue)

    async def unsubscribe(self, queue: asyncio.Queue):
        async with self._subscribers_lock:
            self._subscribers.discard(queue)

    async def _run_with_retries(self):
        while True:
            try:
                await self._run()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Error in camera {self.serial}: {e}")
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

        async for frame in camera:
            # Discard frames if there are no subscribers
            if not self._subscribers:
                continue
            # Skip empty frames
            if len(frame) == 0:
                continue
            # Prepare the item to send to subscribers
            frame_data = CaptureFrame(
                frame.data[:],
                frame.timestamp,
                frame.frame_nb,
            )
            # Create a snapshot of subscribers to avoid holding the lock while publishing
            async with self._subscribers_lock:
                subscribers_snapshot = tuple(self._subscribers)
            # Publish to all subscribers
            for subscriber in subscribers_snapshot:
                try:
                    subscriber.put_nowait(frame_data)
                except asyncio.QueueFull:
                    pass
                except asyncio.QueueShutDown:
                    await self.unsubscribe(subscriber)
