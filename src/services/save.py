"""연속(아카이브) 녹화 서비스.

카메라 1대의 frame을 구독해 start부터 stop까지 끊김 없이 하나의 MP4
(h264) 파일로 녹화한다. trigger 기반 구간 녹화(trigger_save)와 달리
세션 전체를 보관하는 아카이브 용도이다.
"""

import asyncio
import logging
import os
import time
from pathlib import Path
from typing import Optional

from services.capture import CaptureFrame, CaptureService
from utils.ffmpeg import ffmpeg_feed_data, ffmpeg_start, ffmpeg_stop
from utils.misc import format_unix_timestamp

logger = logging.getLogger(__name__)


class SaveService:
    """카메라 1대를 연속 녹화하는 서비스.

    :param capture_service: 녹화할 카메라의 CaptureService
    :param name: 파일명에 timestamp 뒤에 붙일 접미사
    :param stop_timeout: stop 시 queue 소진을 기다리는 최대 시간 (초)
    """

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
        self._ffmpeg_process: Optional[asyncio.subprocess.Process] = None

    async def start(self, save_path: str):
        """ffmpeg를 띄우고 녹화를 시작한다.

        :param save_path: 녹화 파일이 저장될 디렉터리
            (파일명은 <timestamp><name>.mp4)
        """
        if self._save_task is not None:
            logger.warning("SaveService is already started")
            return

        path = Path(save_path) / (format_unix_timestamp(time.time()) + self.name + ".mp4")
        os.makedirs(path.parent, exist_ok=True)

        process = await ffmpeg_start(
            dst=path.as_posix(),
            control=self.capture_service.control,
            encoder="h264",
            log_path=path.with_suffix(".log").as_posix(),
        )
        self._ffmpeg_process = process

        self._queue = asyncio.Queue()
        self._save_task = asyncio.create_task(self._run())
        await self.capture_service.subscribe(self._queue)

    async def stop(self):
        """녹화를 종료한다.

        구독을 해지하고 queue를 shutdown한 뒤, stop_timeout 안에 남은
        frame이 소진되지 않으면 태스크를 강제 취소한다.
        """
        if self._save_task is None:
            logger.warning("SaveService is not running")
            return

        assert self._queue is not None
        assert self._ffmpeg_process is not None

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
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("Error from cancelled save task")
        except Exception as e:
            logger.error(f"Error while stopping SaveService: {e}, cancelling task...")
            self._save_task.cancel()
            try:
                await self._save_task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("Error from cancelled save task")
        finally:
            self._save_task = None

    async def _run(self):
        """queue의 frame을 ffmpeg에 공급하고, 종료 시 ffmpeg를 닫는 본체 루프."""
        assert self._queue is not None
        assert self._ffmpeg_process is not None
        try:
            while True:
                try:
                    frame = await self._queue.get()
                except asyncio.QueueShutDown:
                    return
                try:
                    await ffmpeg_feed_data(self._ffmpeg_process, frame.data)
                except Exception as e:
                    logger.error(f"Error while feeding data to ffmpeg: {e}")
                finally:
                    self._queue.task_done()
        finally:
            await ffmpeg_stop(self._ffmpeg_process)
