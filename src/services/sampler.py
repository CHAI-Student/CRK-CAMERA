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

        self._sampling_task: Optional[asyncio.Task] = None

        self._queue: Optional[asyncio.Queue[CaptureFrame]] = None
        self._ffmpeg_processes: dict[int, asyncio.subprocess.Process] = {}

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

            await asyncio.to_thread(os.makedirs, save_path, exist_ok=True)

            _save_path = Path(save_path)

            _ffmpeg_processes = await asyncio.gather(
                *[
                    ffmpeg_start(
                        dst=(_save_path / f"{idx}.avi").as_posix(),
                        control=cs.control,
                        log_path=(_save_path / f"{idx}.log").as_posix(),
                    )
                    for idx, cs in self.capture_services.items()
                    if idx in cameras
                ],
                return_exceptions=True,
            )

            if any(isinstance(p, BaseException) for p in _ffmpeg_processes):
                # Cleanup any started processes
                for p in _ffmpeg_processes:
                    if isinstance(p, asyncio.subprocess.Process):
                        await ffmpeg_stop(p)
                raise RuntimeError("Failed to start ffmpeg processes")

            def _generator():
                for p in _ffmpeg_processes:
                    assert isinstance(p, asyncio.subprocess.Process)
                    yield p

            self._ffmpeg_processes = dict(
                zip([idx for idx in self.capture_services if idx in cameras], _generator())
            )

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
                await asyncio.gather(*[
                    ffmpeg_stop(process)
                    for process in self._ffmpeg_processes.values()
                ])
                self._sampling_task = None

    async def _run_with_retries(self):
        while True:
            try:
                await self._run()
                return
            except asyncio.CancelledError:
                raise
            except Exception as e:
                for process in self._ffmpeg_processes.values():
                    if not await is_running(process):
                        logger.error("ffmpeg process has exited unexpectedly")
                        await self.stop()
                        return
                logger.error(f"Error in SaveService, restarting: {e}")
                await asyncio.sleep(1)

    async def _run(self):
        assert self._sampling_task is not None
        assert self._queue is not None
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
                await ffmpeg_feed_data(self._ffmpeg_processes[key], frame.data)
            finally:
                self._queue.task_done()


async def is_running(proc):
    with contextlib.suppress(asyncio.TimeoutError):
        await asyncio.wait_for(proc.wait(), 1e-6)
    return proc.returncode is None
