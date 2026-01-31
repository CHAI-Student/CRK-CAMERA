import asyncio
import logging
import os
from collections import deque
from enum import Enum
from pathlib import Path
from typing import Optional

from services.capture import CaptureFrame, CaptureService
from utils.ffmpeg import (
    ffmpeg_feed_data,
    ffmpeg_start,
    ffmpeg_stop,
)
from utils.misc import unix_timestamp_to_iso8601

logger = logging.getLogger(__name__)


class SaveState(Enum):
    IDLE = 0  # Waiting for trigger
    TRIGGERED = 1  # Buffering frames to be saved, creates ffmpeg process to save
    SAVING = 2  # Currently saving frames to disk, use existing ffmpeg process


# ONLY IDLE -> TRIGGERED - > SAVING - > IDLE transitions allowed


class SaveService:
    def __init__(
        self,
        capture_service: CaptureService,
        replay_duration: float = 1.0,
        stop_timeout: float = 5.0,
    ):
        self.capture_service = capture_service
        self.stop_timeout = stop_timeout

        self.save_path: Optional[Path] = None

        self._replay_buffer: deque[CaptureFrame] = deque(
            maxlen=int(replay_duration * capture_service.fps)
        )

        self._save_task: Optional[asyncio.Task] = None
        self._queue: Optional[asyncio.Queue] = None

        self._state: SaveState = SaveState.IDLE
        self._on_finish: Optional[asyncio.Event] = None
        self._save_until: float = 0.0

        self._ffmpeg_process: Optional[asyncio.subprocess.Process] = None

    async def start(self, save_path: str):
        """
        Start the save service.

        :param self: The SaveService instance
        :param save_path: The path where the video will be saved
        :type save_path: str

        :raises OSError: If the save directory cannot be created
        """
        if self._save_task is not None:
            logger.warning("Save service is already running")
            return

        self.save_path = Path(save_path)
        try:
            os.makedirs(self.save_path, exist_ok=True)
        except Exception as e:
            logger.error(f"Failed to create save directory {self.save_path}: {e}")
            return

        self._queue = asyncio.Queue()
        self._save_task = asyncio.create_task(self._run())
        await self.capture_service.subscribe(self._queue)

    async def stop(self):
        """
        Stop the save service.

        :param self: The SaveService instance
        """
        if self._save_task is None:
            logger.warning("No save task found")
            return

        assert self._queue is not None

        await self.capture_service.unsubscribe(self._queue)
        self._queue.shutdown()

        async def _cleanup_procedure(q, t):
            await q.join()
            await t

        try:
            await asyncio.wait_for(
                _cleanup_procedure(self._queue, self._save_task),
                timeout=self.stop_timeout,
            )
        except asyncio.TimeoutError:
            logger.warning("Timeout while stopping save service, cancelling task")
            self._save_task.cancel()
            try:
                await self._save_task
            except asyncio.CancelledError:
                pass
        finally:
            self._save_task = None
            self._queue = None

    def trigger(self, duration: float) -> Optional[asyncio.Event]:
        """
        Trigger saving video for the specified duration.

        :param self: The SaveService instance
        :param duration: The duration for which saving should be triggered (in seconds)
        :type duration: float
        :return: An asyncio.Event that will be set when saving is finished, or None if extending an existing save
        :rtype: Optional[asyncio.Event]
        """
        if self._save_task is None:
            return None

        if self._state == SaveState.IDLE:
            # start new saving
            self._state = SaveState.TRIGGERED
            self._on_finish = asyncio.Event()
            self._save_until = asyncio.get_event_loop().time() + duration
            return self._on_finish
        elif self._state in (SaveState.TRIGGERED, SaveState.SAVING):
            # extend existing saving
            self._save_until = max(
                self._save_until, asyncio.get_event_loop().time() + duration
            )
            return None
        else:
            raise ValueError(f"Invalid state {self._state} in SaveService")

    async def _run(self):
        assert self._queue is not None

        self._replay_buffer.clear()

        try:
            while True:
                frame: CaptureFrame = await self._queue.get()
                assert isinstance(frame, CaptureFrame)

                # always keep updating replay buffer
                self._replay_buffer.append(frame)

                if self._state == SaveState.IDLE:
                    continue
                elif self._state == SaveState.TRIGGERED:
                    assert self.save_path is not None

                    save_filename = self.save_path / (
                        unix_timestamp_to_iso8601(frame.timestamp) + ".avi"
                    )
                    # start saving process
                    self._ffmpeg_process = await ffmpeg_start(
                        dst=save_filename.as_posix(),
                        width=self.capture_service.width,
                        height=self.capture_service.height,
                        fps=self.capture_service.fps,
                    )
                    # switch to saving state
                    self._state = SaveState.SAVING
                    # feed replay buffer to ffmpeg
                    for buffered_frame in self._replay_buffer:
                        await ffmpeg_feed_data(
                            self._ffmpeg_process, buffered_frame.data
                        )
                elif self._state == SaveState.SAVING:
                    assert self._ffmpeg_process is not None
                    assert self._on_finish is not None
                    # continue saving
                    await ffmpeg_feed_data(self._ffmpeg_process, frame.data)
                    # check if we should stop saving
                    if asyncio.get_event_loop().time() >= self._save_until:
                        # finish saving
                        self._state = SaveState.IDLE
                        await ffmpeg_stop(self._ffmpeg_process)
                        self._ffmpeg_process = None
                        self._on_finish.set()
                        self._on_finish = None
                else:
                    raise ValueError(f"Invalid state {self._state} in SaveService")

                self._queue.task_done()
        finally:
            # cleanup on exit
            self._state = SaveState.IDLE
            if self._ffmpeg_process is not None:
                await ffmpeg_stop(self._ffmpeg_process)
                self._ffmpeg_process = None
            if self._on_finish is not None:
                self._on_finish.set()
                self._on_finish = None
