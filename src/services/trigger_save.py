"""loadcell trigger 기반 구간 녹화 서비스.

여러 카메라(top/side)의 frame을 replay buffer에 상시 보관하다가,
loadcell change로 trigger가 발화하면 buffer 내용(pre-roll)부터 ffmpeg로
흘려보내 trigger 전후 구간을 AVI로 저장한다. 저장 중 trigger가 다시
오면 종료 시각(save_until)을 늘려 하나의 episode로 병합한다.

상태 기계 (state pattern):

- IdleState: session 없음. trigger/frame을 모두 무시.
- ListeningState: session 열림. trigger가 오면 녹화를 시작하고 SavingState로 전이.
- SavingState: 녹화 중. frame을 ffmpeg에 공급하고, save_until이 지나면
  녹화를 닫고 ListeningState로 복귀.
"""

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
    """trigger 1회(episode 시작)에 대해 호출자에게 돌려주는 핸들.

    :param event: 녹화가 끝나면 set되는 asyncio.Event
    :param paths: 카메라 key("top"/"side")별 저장 파일 경로
    :param change_timestamps: 이 episode를 시작했거나 연장한 모든 loadcell
        change의 wall-clock(IO-BOARD 시계) timestamp 목록.
        save_until은 monotonic(loop.time()) 기준이므로 두 시간축을 절대
        섞어 쓰면 안 된다.
    """

    event: asyncio.Event
    paths: dict[str, Path]
    change_timestamps: list[float]


class BaseState(metaclass=ABCMeta):
    """TriggerSaveService 상태 공통 인터페이스."""

    @abstractmethod
    async def trigger(self, duration: float, ts: float) -> Optional[TriggerEvent]:
        pass

    @abstractmethod
    async def frame(self, frame: CaptureFrame) -> None:
        pass

    @abstractmethod
    async def shutdown(self) -> None:
        pass


class IdleState(BaseState):
    """session이 열리지 않은 상태. trigger와 frame을 모두 무시한다."""

    def __init__(self, save_service: "TriggerSaveService"):
        self.save_service = save_service

    async def trigger(self, duration: float, ts: float) -> None:
        pass

    async def frame(self, frame: CaptureFrame) -> None:
        pass

    async def shutdown(self) -> None:
        pass


class ListeningState(BaseState):
    """session이 열려 trigger를 기다리는 상태."""

    def __init__(self, save_service: "TriggerSaveService", save_directory: Path):
        self.save_service = save_service
        self.save_directory = save_directory

    async def trigger(self, duration: float, ts: float) -> TriggerEvent:
        """녹화를 시작하고 SavingState로 전이한다.

        카메라별 ffmpeg 프로세스를 띄우고 replay buffer(pre-roll)를 먼저
        흘려보낸 뒤, 이후 frame 공급은 SavingState가 이어받는다.
        """
        capture_services = self.save_service.capture_services
        timestamp = format_unix_timestamp(time.time())

        # 카메라 key별 저장 경로: <save_directory>/<timestamp>/<key>.avi
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
            # 일부만 시작에 성공했으면 그 프로세스들을 정리하고 실패 처리한다
            for p in _ffmpeg_processes:
                if isinstance(p, asyncio.subprocess.Process):
                    await ffmpeg_stop(p)
            raise RuntimeError("Failed to start ffmpeg processes")

        def _generator():
            for p in _ffmpeg_processes:
                assert isinstance(p, asyncio.subprocess.Process)
                yield p

        ffmpeg_processes = dict(zip(save_paths.keys(), _generator()))

        # replay buffer(pre-roll)에 쌓인 frame들을 먼저 ffmpeg에 흘려보낸다
        async def _flush(process, buffer):
            for frame in buffer:
                await ffmpeg_feed_data(process, frame.data)

        await asyncio.gather(*[
            _flush(ffmpeg_processes[key], buffer)
            for key, buffer in self.save_service._replay_buffers.items()
        ])

        on_finish = asyncio.Event()
        save_until = asyncio.get_running_loop().time() + duration

        # SavingState와 같은 list 객체를 공유한다. 연장 trigger가 append한
        # timestamp가 호출자가 받은 TriggerEvent에도 그대로 반영되게 하기 위함.
        change_timestamps = [ts]

        self.save_service._state = SavingState(
            self.save_service, self.save_directory, on_finish, save_until,
            ffmpeg_processes, change_timestamps,
        )

        return TriggerEvent(on_finish, save_paths, change_timestamps)

    async def frame(self, frame: CaptureFrame) -> None:
        pass

    async def shutdown(self) -> None:
        pass


class SavingState(BaseState):
    """녹화가 진행 중인 상태."""

    def __init__(
        self,
        save_service: "TriggerSaveService",
        save_directory: Path,
        on_finish: asyncio.Event,
        save_until: float,
        ffmpeg_processes: Mapping[str, asyncio.subprocess.Process],
        change_timestamps: list[float],
    ):
        self.save_service = save_service
        self.save_directory = save_directory
        self.on_finish = on_finish
        self.save_until = save_until
        self.ffmpeg_processes = ffmpeg_processes
        self.change_timestamps = change_timestamps

    async def trigger(self, duration: float, ts: float) -> None:
        """연장 trigger: 종료 시각을 늘리고 change timestamp를 기록한다."""
        self.save_until = max(
            self.save_until, asyncio.get_running_loop().time() + duration
        )
        # 서비스의 state lock 아래에서 호출되므로 shutdown과 경합하지 않는다.
        self.change_timestamps.append(ts)

    async def frame(self, frame: CaptureFrame) -> None:
        key = self.save_service._reverse_mapping[frame.serial]
        # 녹화 계속: frame을 해당 카메라의 ffmpeg에 공급
        await ffmpeg_feed_data(self.ffmpeg_processes[key], frame.data)
        # save_until이 지났으면 녹화 종료
        if asyncio.get_running_loop().time() >= self.save_until:
            await self.shutdown()

    async def shutdown(self) -> None:
        """ffmpeg들을 닫아 파일을 마무리하고 ListeningState로 복귀한다."""
        await asyncio.gather(*[
            ffmpeg_stop(process)
            for process in self.ffmpeg_processes.values()
        ])
        self.on_finish.set()
        self.save_service._state = ListeningState(self.save_service, self.save_directory)


class TriggerSaveService:
    """trigger 기반 구간 녹화 서비스 본체.

    :param capture_services: 카메라 key("top"/"side")별 CaptureService
    :param stop_timeout: stop 시 queue 소진을 기다리는 최대 시간 (초)
    :param replay_duration: trigger 이전 pre-roll로 보관할 frame 길이 (초)
    """

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
        """서비스를 시작한다. 모든 카메라를 구독하고 frame 소비 태스크를 띄운다."""
        if self._save_task is not None:
            logger.warning("Save service is already running")
            return

        self._queue = asyncio.Queue(maxsize=90)
        self._save_task = asyncio.create_task(self._run_with_retries())
        for cs in self.capture_services.values():
            await cs.subscribe(self._queue)

    async def stop(self):
        """서비스를 종료한다.

        구독을 해지하고 queue를 shutdown한 뒤, stop_timeout 안에 남은
        frame이 소진되지 않으면 태스크를 강제 취소한다.
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
        """녹화 session을 연다 (Idle → Listening).

        :param save_path: 녹화 파일이 저장될 디렉터리 경로
        :raises OSError: 저장 디렉터리 생성에 실패한 경우
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
        """녹화 session을 닫는다 (→ Idle). 녹화 중이면 강제로 마무리한다."""
        if self._save_task is None:
            logger.warning("Save service is not running")
            return

        async with self._state_lock:
            if isinstance(self._state, IdleState):
                logger.warning("Save service is not in a session")
                return

            await self._state.shutdown()

            self._state = IdleState(self)

    async def trigger(self, duration: float, ts: float) -> Optional[TriggerEvent]:
        """지정한 시간 동안 녹화를 trigger한다.

        :param duration: trigger 시점부터 녹화를 유지할 시간 (초, post-roll)
        :param ts: 이 trigger를 유발한 loadcell change의 wall-clock timestamp
        :return: 새 episode가 시작되면 TriggerEvent,
            기존 녹화를 연장했거나 서비스/session이 꺼져 있으면 None
        """
        if self._save_task is None:
            return None

        async with self._state_lock:
            return await self._state.trigger(duration, ts)

    async def _run_with_retries(self):
        """_run을 감싸 예외 발생 시 1초 후 재시작한다."""
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
        """queue에서 frame을 꺼내 현재 state에 전달하고 replay buffer에 쌓는 본체 루프."""
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
