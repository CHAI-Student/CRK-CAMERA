import asyncio
import logging
import os
import time
from abc import ABCMeta, abstractmethod
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional

from services.capture import CaptureFrame, CaptureService
from utils.ffmpeg import ffmpeg_feed_data, ffmpeg_start, ffmpeg_stop
from utils.misc import format_unix_timestamp

logger = logging.getLogger(__name__)


@dataclass
class TriggerEvent:
    event: asyncio.Event
    paths: dict[str, Path]


class BaseState(metaclass=ABCMeta):
    @abstractmethod
    async def trigger(self, duration: float) -> Optional[TriggerEvent]:
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
    
    async def trigger(self, duration: float) -> None:
        pass
    
    async def frame(self, frame: CaptureFrame) -> None:
        pass
    
    async def shutdown(self) -> None:
        pass


class ListeningState(BaseState):
    def __init__(self, save_service: "TriggerSaveService", save_directory: Path):
        self.save_service = save_service
        self.save_directory = save_directory

    async def trigger(self, duration: float) -> TriggerEvent:
        capture_services = self.save_service.capture_services
        timestamp = format_unix_timestamp(time.time())

        save_paths = {
            key: self.save_directory / timestamp / (key + ".avi")
            for key in capture_services
        }

        await asyncio.gather(*[
            asyncio.to_thread(os.makedirs, path.parent, exist_ok=True)
            for path in save_paths.values()
        ])
        
        _ffmpeg_processes = await asyncio.gather(*[
            ffmpeg_start(
                dst=path.as_posix(),
                control=cs.control,
                log_path=path.with_suffix(".log").as_posix(),
            )
            for path, cs in zip(save_paths.values(), capture_services.values())
        ], return_exceptions=True)

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
        
        ffmpeg_processes = dict(zip(save_paths.keys(), _generator()))

        async def _flush(process, buffer):
            for frame in buffer:
                await ffmpeg_feed_data(process, frame.data)
        
        await asyncio.gather(*[
            _flush(ffmpeg_processes[key], buffer)
            for key, buffer in self.save_service._replay_buffers.items()
        ])

        on_finish = asyncio.Event()
        save_until = asyncio.get_running_loop().time() + duration

        self.save_service._state = SavingState(
            self.save_service, self.save_directory, on_finish, save_until, ffmpeg_processes
        )

        return TriggerEvent(on_finish, save_paths)

    async def frame(self, frame: CaptureFrame) -> None:
        pass

    async def shutdown(self) -> None:
        pass


class SavingState(BaseState):
    def __init__(
        self,
        save_service: "TriggerSaveService",
        save_directory: Path,
        on_finish: asyncio.Event,
        save_until: float,
        ffmpeg_processes: Mapping[str, asyncio.subprocess.Process],
    ):
        self.save_service = save_service
        self.save_directory = save_directory
        self.on_finish = on_finish
        self.save_until = save_until
        self.ffmpeg_processes = ffmpeg_processes

    async def trigger(self, duration: float) -> None:
        self.save_until = max(
            self.save_until, asyncio.get_running_loop().time() + duration
        )

    async def frame(self, frame: CaptureFrame) -> None:
        key = self.save_service._reverse_mapping[frame.serial]
        # continue saving
        await ffmpeg_feed_data(self.ffmpeg_processes[key], frame.data)
        # check if we should stop saving
        if asyncio.get_running_loop().time() >= self.save_until:
            await self.shutdown()

    async def shutdown(self) -> None:
        await asyncio.gather(*[
            ffmpeg_stop(process)
            for process in self.ffmpeg_processes.values()
        ])
        self.on_finish.set()
        self.save_service._state = ListeningState(self.save_service, self.save_directory)


class TriggerSaveService:
    def __init__(
        self,
        capture_services: dict[str, CaptureService],
        stop_timeout: float = 5.0,
        replay_duration: float = 4.0,
    ):
        self.capture_services = capture_services
        self._reverse_mapping = {v.serial: k for k, v in capture_services.items()}
        self.stop_timeout = stop_timeout

        self._save_task: Optional[asyncio.Task] = None

        self._queue: Optional[asyncio.Queue[CaptureFrame]] = None

        self._replay_buffers: dict[str, deque[CaptureFrame]] = {
            k: deque(maxlen=int(replay_duration * cs.control.fps))
            for k, cs in capture_services.items()
        }

        self._state_lock = asyncio.Lock()
        self._state: BaseState = IdleState(self)
    
    async def start(self):
        """
        Start the save service.

        :param self: The SaveService instance
        """
        if self._save_task is not None:
            logger.warning("Save service is already running")
            return
        
        self._queue = asyncio.Queue(maxsize=90)
        self._save_task = asyncio.create_task(self._run_with_retries())
        for cs in self.capture_services.values():
            await cs.subscribe(self._queue)
    
    async def stop(self):
        """
        Stop the save service.

        :param self: The SaveService instance
        """
        if self._save_task is None:
            logger.warning("No save task found")
            return

        assert self._queue is not None

        for cs in self.capture_services.values():
            await cs.unsubscribe(self._queue)
        self._queue.shutdown()

        try:
            async with asyncio.timeout(self.stop_timeout):
                await self._queue.join()
                await self._save_task
        except asyncio.TimeoutError:
            logger.warning("Timeout while stopping save service, cancelling task")
            self._save_task.cancel()
            try:
                await self._save_task
            except asyncio.CancelledError:
                pass
        except Exception as e:
            logger.error(f"Error while stopping save service: {e}, cancelling task...")
            self._save_task.cancel()
            try:
                await self._save_task
            except asyncio.CancelledError:
                pass
        finally:
            async with self._state_lock:
                self._state = IdleState(self)
            self._save_task = None

    async def start_session(self, save_path: str):
        """
        Start the save session.

        :param self: The SaveService instance
        :param save_path: The path where the video will be saved
        :type save_path: str

        :raises OSError: If the save directory cannot be created
        """
        if self._save_task is None:
            logger.warning("Save service is not running")
            return
        
        async with self._state_lock:
            if not isinstance(self._state, IdleState):
                logger.warning("Save service is already in a session")
                return
            
            save_directory = Path(save_path)
            os.makedirs(save_directory, exist_ok=True)

            self._state = ListeningState(self, save_directory)

    async def stop_session(self):
        """
        Stop the save session.

        :param self: The SaveService instance
        """
        if self._save_task is None:
            logger.warning("Save service is not running")
            return
        
        async with self._state_lock:
            if isinstance(self._state, IdleState):
                logger.warning("Save service is not in a session")
                return
            
            await self._state.shutdown()
            
            self._state = IdleState(self)

    async def trigger(self, duration: float) -> Optional[TriggerEvent]:
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
        
        async with self._state_lock:
            return await self._state.trigger(duration)

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
        assert self._save_task is not None
        assert self._queue is not None
        try:
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
                    async with self._state_lock:
                        await self._state.frame(frame)
                        self._replay_buffers[key].append(frame)
                finally:
                    self._queue.task_done()
        finally:
            async with self._state_lock:
                await self._state.shutdown()
