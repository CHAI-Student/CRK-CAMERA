import asyncio
import contextlib
import logging
import os
from pathlib import Path
from typing import Optional

from services.capture import CaptureFrame, CaptureService
from utils.ffmpeg import ffmpeg_feed_data, ffmpeg_start, ffmpeg_stop

logger = logging.getLogger(__name__)


class SamplerService:
    def __init__(
        self,
        capture_services: dict[int, CaptureService],
        stop_timeout: float = 5.0,
    ):
        self.capture_services = capture_services
        self._reverse_mapping = {v.serial: k for k, v in capture_services.items()}
        self.stop_timeout = stop_timeout

        self._save_path: Optional[Path] = None

        self._sampling_task: Optional[asyncio.Task] = None
        self._saving_tasks: set[asyncio.Task] = set()

        self._queue: Optional[asyncio.Queue[CaptureFrame]] = None

        self._lock = asyncio.Lock()

    async def start(self, save_path: str, cameras: Optional[list[int]] = None):
        """
        Start the save service.

        :param self: The SaveService instance
        """
        if cameras is None:
            cameras = [0, 1]

        async with self._lock:
            if self._sampling_task is not None:
                logger.warning("Save service is already running")
                return

            self._save_path = Path(save_path)
            self._queue = asyncio.Queue(maxsize=90)
            self._sampling_task = asyncio.create_task(self._run_with_retries())
            for camera_id in cameras:
                cs = self.capture_services.get(camera_id)
                if cs is not None:
                    await cs.subscribe(self._queue)

    async def stop(self):
        """
        Stop the save service.

        :param self: The SaveService instance
        """
        async with self._lock:
            if self._sampling_task is None:
                logger.warning("No save task found")
                return

            assert self._queue is not None

            for cs in self.capture_services.values():
                await cs.unsubscribe(self._queue)
            self._queue.shutdown()

            try:
                if asyncio.current_task() is self._sampling_task:
                    logger.warning("Save service stop called from within sampling task, skipping wait")
                else:
                    async with asyncio.timeout(self.stop_timeout):
                        await self._queue.join()
                        await self._sampling_task
            except asyncio.TimeoutError:
                logger.warning("Timeout while stopping save service, cancelling task")
                self._sampling_task.cancel()
                try:
                    await self._sampling_task
                except asyncio.CancelledError:
                    pass
            except Exception as e:
                logger.error(f"Error while stopping save service: {e}, cancelling task...")
                self._sampling_task.cancel()
                try:
                    await self._sampling_task
                except asyncio.CancelledError:
                    pass
            finally:
                await asyncio.gather(*self._saving_tasks, return_exceptions=True)
                self._sampling_task = None

    async def _run_with_retries(self):
        while True:
            try:
                await self._run()
                return
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Error in SaveService, restarting: {e}")
                await asyncio.sleep(1)

    async def _run(self):
        assert self._save_path is not None
        assert self._sampling_task is not None
        assert self._queue is not None
        frame_numbers = {key: 0 for key in self.capture_services.keys()}
        while True:
            try:
                frame = await self._queue.get()
            except asyncio.QueueShutDown:
                return
            try:
                key = self._reverse_mapping[frame.serial]
            except KeyError:
                logger.warning(f"Received frame from unknown serial {frame.serial}")
                self._queue.task_done()
                continue
            try:
                camera_control = self.capture_services[key].control
                width, height = camera_control.width, camera_control.height
                task = asyncio.create_task(asyncio.to_thread(_write_frame_to_file, self._save_path, key, frame.pixel_format, bytes(frame.data), frame_numbers.setdefault(key, 0), width, height))
                self._saving_tasks.add(task)
                task.add_done_callback(self._saving_tasks.discard)
                frame_numbers[key] += 1
            finally:
                self._queue.task_done()

async def is_running(proc):
    with contextlib.suppress(asyncio.TimeoutError):
        await asyncio.wait_for(proc.wait(), 1e-6)
    return proc.returncode is None

def _write_frame_to_file(save_path: Path, camera_id: int, pixel_format: str, data: bytes, index: int, width: int, height: int):
    camera_path = save_path / f"cam_{camera_id}"
    camera_path.mkdir(parents=True, exist_ok=True)
    frame_path = camera_path / f"{index:06d}.jpg"
    if pixel_format == "JPEG" or pixel_format == "MJPEG":
        with open(frame_path, "wb") as f:
            f.write(data)
    elif pixel_format == "YUYV":
        # Convert YUYV to JPEG
        import cv2
        import numpy as np
        yuyv_image = np.frombuffer(data, dtype=np.uint8)
        yuyv_image = yuyv_image.reshape((height, width, 2))
        bgr_image = cv2.cvtColor(yuyv_image, cv2.COLOR_YUV2BGR_YUYV)
        ret, jpeg_image = cv2.imencode('.jpg', bgr_image)
        if not ret:
            raise ValueError("Failed to encode YUYV image to JPEG")
        with open(frame_path, "wb") as f:
            f.write(jpeg_image.tobytes())