import asyncio
import logging
import os
import time
from pathlib import Path
from typing import Optional

from services.capture import CaptureFrame, CaptureService
from utils.gst_pipeline import gst_pipeline_feed_data, gst_pipeline_start, gst_pipeline_stop
from utils.misc import format_unix_timestamp

logger = logging.getLogger(__name__)


class SaveService:
    def __init__(
        self,
        capture_service: CaptureService,
        name: str,
        stop_timeout: float = 5.0,
    ):
        self.capture_service = capture_service
        self.name = name
        self.stop_timeout = stop_timeout

        self._save_task: Optional[asyncio.Task] = None
        self._queue: Optional[asyncio.Queue[CaptureFrame]] = None
        self._pipeline_processes: Optional[tuple[asyncio.subprocess.Process, asyncio.subprocess.Process]] = None

    async def start(self, save_path: str):
        if self._save_task is not None:
            logger.warning("SaveService is already started")
            return

        path = Path(save_path) / (format_unix_timestamp(time.time()) + self.name + ".mp4")
        os.makedirs(path.parent, exist_ok=True)

        processes = await gst_pipeline_start(self.capture_service.control, dst=path.as_posix(), log_path=path.with_suffix(".log").as_posix())
        self._pipeline_processes = processes

        self._queue = asyncio.Queue()
        self._save_task = asyncio.create_task(self._run())
        await self.capture_service.subscribe(self._queue)

    async def stop(self):
        if self._save_task is None:
            logger.warning("SaveService is not running")
            return

        assert self._queue is not None
        assert self._pipeline_processes is not None

        await self.capture_service.unsubscribe(self._queue)
        self._queue.shutdown()

        try:
            async with asyncio.timeout(self.stop_timeout):
                await self._queue.join()
                await self._save_task
        except asyncio.TimeoutError:
            logger.error("Timeout while stopping SaveService")
            self._save_task.cancel()
            try:
                await self._save_task
            except:
                pass
        except Exception as e:
            logger.error(f"Error while stopping SaveService: {e}, cancelling task...")
            self._save_task.cancel()
            try:
                await self._save_task
            except:
                pass
        finally:
            self._save_task = None

    async def _run(self):
        assert self._queue is not None
        assert self._pipeline_processes is not None
        try:
            while True:
                try:
                    frame = await self._queue.get()
                except asyncio.QueueShutDown:
                    return
                try:
                    await gst_pipeline_feed_data(self._pipeline_processes[0], frame.data)
                except Exception as e:
                    logger.error(f"Error while feeding data to gst_pipeline: {e}")
                finally:
                    self._queue.task_done()
        finally:
            await gst_pipeline_stop(self._pipeline_processes[0], self._pipeline_processes[1])
