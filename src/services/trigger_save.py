import asyncio
import logging
import os
from abc import ABCMeta, abstractmethod
from collections import deque
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


class BaseState(metaclass=ABCMeta):
    @abstractmethod
    async def trigger(self, duration: float) -> Optional[asyncio.Event]:
        pass

    @abstractmethod
    async def frame(self, frame: CaptureFrame) -> None:
        pass

    @abstractmethod
    async def shutdown(self) -> None:
        pass


class IdleState(BaseState):
    def __init__(self, save_service: "TriggerSaveService"):
        self.save_service = save_service

    async def trigger(self, duration: float) -> asyncio.Event:
        on_finish = asyncio.Event()
        save_until = asyncio.get_running_loop().time() + duration
        self.save_service.state = TriggeredState(
            self.save_service, on_finish, save_until
        )
        return on_finish

    async def frame(self, frame: CaptureFrame) -> None:
        pass

    async def shutdown(self) -> None:
        pass


class TriggeredState(BaseState):
    def __init__(
        self,
        save_service: "TriggerSaveService",
        on_finish: asyncio.Event,
        save_until: float,
    ):
        self.save_service = save_service
        self.on_finish = on_finish
        self.save_until = save_until

    async def trigger(self, duration: float) -> None:
        self.save_until = max(
            self.save_until, asyncio.get_running_loop().time() + duration
        )

    async def frame(self, frame: CaptureFrame) -> None:
        if self.save_service.save_path is None:
            raise RuntimeError("Save path is not set")
        # create save filename
        save_filename = self.save_service.save_path / (
            unix_timestamp_to_iso8601(frame.timestamp) + ".avi"
        )
        # start saving process
        ffmpeg_process = await ffmpeg_start(
            dst=save_filename.as_posix(),
            width=self.save_service.capture_service.width,
            height=self.save_service.capture_service.height,
            fps=self.save_service.capture_service.fps,
        )
        # feed replay buffer to ffmpeg
        for buffered_frame in self.save_service._replay_buffer:
            await ffmpeg_feed_data(ffmpeg_process, buffered_frame.data)
        # transition to SAVING state
        self.save_service.state = SavingState(
            self.save_service, self.on_finish, self.save_until, ffmpeg_process
        )

    async def shutdown(self) -> None:
        self.on_finish.set()
        self.save_service.state = IdleState(self.save_service)


class SavingState(BaseState):
    def __init__(
        self,
        save_service: "TriggerSaveService",
        on_finish: asyncio.Event,
        save_until: float,
        ffmpeg_process: asyncio.subprocess.Process,
    ):
        self.save_service = save_service
        self.on_finish = on_finish
        self.save_until = save_until
        self.ffmpeg_process = ffmpeg_process

    async def trigger(self, duration: float) -> None:
        self.save_until = max(
            self.save_until, asyncio.get_running_loop().time() + duration
        )

    async def frame(self, frame: CaptureFrame) -> None:
        # continue saving
        await ffmpeg_feed_data(self.ffmpeg_process, frame.data)
        # check if we should stop saving
        if asyncio.get_running_loop().time() >= self.save_until:
            await self.shutdown()

    async def shutdown(self) -> None:
        await ffmpeg_stop(self.ffmpeg_process)
        self.on_finish.set()
        self.save_service.state = IdleState(self.save_service)


class TriggerSaveService:
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
        self._queue: asyncio.Queue[CaptureFrame] = asyncio.Queue()

        self.state_lock = asyncio.Lock()
        self.state: BaseState = IdleState(self)

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
        os.makedirs(self.save_path, exist_ok=True)

        self._queue: asyncio.Queue[CaptureFrame] = asyncio.Queue()
        self._save_task = asyncio.create_task(self._run_with_retries())
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

    async def trigger(self, duration: float) -> Optional[asyncio.Event]:
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

        async with self.state_lock:
            return await self.state.trigger(duration)

    async def _run_with_retries(self):
        while True:
            try:
                await self._run()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Error in SaveService, restarting: {e}")
                await asyncio.sleep(1)

    async def _run(self):
        assert self._save_task is not None
        assert self.save_path is not None
        self._replay_buffer.clear()
        try:
            while True:
                try:
                    frame = await self._queue.get()
                except asyncio.QueueShutDown:
                    break
                try:
                    self._replay_buffer.append(frame)
                    async with self.state_lock:
                        await self.state.frame(frame)
                finally:
                    self._queue.task_done()
        finally:
            async with self.state_lock:
                await self.state.shutdown()
