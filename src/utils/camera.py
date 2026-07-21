"""V4L2 카메라 열기/설정/frame 스트리밍 유틸리티.

linuxpy 기반으로 카메라 장치를 열어 해상도·픽셀 포맷·fps를 설정하고,
frame을 async generator로 내보낸다.
"""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator

import pyudev
from linuxpy.video.device import Device, Frame, VideoCapture

from utils.device import capture_device_from_serial


@dataclass
class CameraControl:
    """카메라 캡처 설정.

    :param width: frame 가로 해상도 (픽셀)
    :param height: frame 세로 해상도 (픽셀)
    :param format: 픽셀 포맷 (예: "MJPG", "YUYV")
    :param fps: 목표 frame rate
    :param extra: 추가 V4L2 control 값 (예: power_line_frequency).
        장치가 지원하지 않는 항목은 무시된다.
    """

    width: int = 640
    height: int = 480
    format: str = "YUYV"
    fps: int = 30
    extra: dict[str, Any] = field(default_factory=dict)


async def run_camera(
    ctx: pyudev.Context,
    serial: str,
    index: int,
    control: CameraControl = CameraControl(),
) -> AsyncGenerator[Frame]:
    """지정한 serial의 카메라를 열어 frame을 yield한다.

    :param ctx: pyudev 장치 데이터베이스 연결
    :param serial: 카메라 장치의 serial 번호
    :param index: 같은 serial을 가진 캡처 노드 중 몇 번째를 쓸지의 index
    :param control: 캡처 설정, 기본값 CameraControl()

    :yield: 카메라에서 캡처된 frame

    :raises DeviceNotFoundError: 해당 serial의 장치를 찾지 못한 경우
    :raises OSError: 장치 접근 중 오류가 발생한 경우
    :raises asyncio.CancelledError: 작업이 취소된 경우
    :raises TimeoutError: frame 수신이 timeout을 초과한 경우
    """

    device = capture_device_from_serial(ctx, serial, index)

    async with _to_async(device):
        stream = VideoCapture(device)

        stream.set_format(control.width, control.height, control.format)
        stream.set_fps(control.fps)
        _apply_controls(device, control.extra)

        async with _to_async(stream):
            assert stream.buffer is not None
            async with stream.buffer.frame_reader:
                # 첫 frame은 스트림 기동 시간을 고려해 넉넉히(3초) 기다린다
                async with asyncio.timeout(3):
                    yield await stream.buffer.frame_reader.aread()

                # 이후 frame은 1초 timeout으로 수신한다
                while True:
                    async with asyncio.timeout(1):
                        yield await stream.buffer.frame_reader.aread()


def _apply_controls(device: Device, controls: dict[str, Any]) -> None:
    """추가 V4L2 control 값을 적용한다. 장치가 지원하지 않는 key는 무시한다."""
    if device.controls is not None:
        for key, value in controls.items():
            try:
                device.controls[key].value = value
            except KeyError:
                pass


@asynccontextmanager
async def _to_async(cm):
    """동기 context manager의 enter/exit를 스레드에서 실행해 async로 감싼다."""
    try:
        yield await asyncio.to_thread(cm.__enter__)
    finally:
        await asyncio.to_thread(cm.__exit__, None, None, None)
