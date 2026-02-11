import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator

import pyudev
from linuxpy.video.device import Device, Frame, VideoCapture

from utils.device import capture_device_from_serial


@dataclass
class CameraControl:
    width: int = 640
    height: int = 480
    format: str = "YUYV"
    fps: int = 30
    extra: dict[str, Any] = field(default_factory=dict)


async def run_camera(
    ctx: pyudev.Context,
    serial: str,
    control: CameraControl = CameraControl(),
) -> AsyncGenerator[Frame]:
    """
    Run the camera with the given serial number and yield frames.

    :param ctx: pyudev device database connection
    :type ctx: pyudev.Context
    :param serial: serial number of the camera device
    :type serial: str
    :param control: control settings of the video frame, defaults to CameraControl()
    :type control: CameraControl, optional

    :yield: Frames captured from the camera
    :rtype: AsyncGenerator[Frame]

    :raises DeviceNotFoundError: If the device with the given serial is not found
    :raises OSError: If there is an error accessing the device
    :raises asyncio.CancelledError: If the operation is cancelled
    :raises asyncio.QueueFull: If the internal queue is full
    """

    device = capture_device_from_serial(ctx, serial)

    async with _to_async(device):
        stream = VideoCapture(device)

        stream.set_format(control.width, control.height, control.format)
        stream.set_fps(control.fps)
        _apply_controls(device, control.extra)

        async with _to_async(stream):
            assert stream.buffer is not None
            async with stream.buffer.frame_reader:
                while True:
                    yield await stream.buffer.frame_reader.aread()


def _apply_controls(device: Device, controls: dict[str, Any]) -> None:
    if device.controls is not None:
        for key, value in controls.items():
            try:
                device.controls[key].value = value
            except KeyError:
                pass


@asynccontextmanager
async def _to_async(cm):
    try:
        yield await asyncio.to_thread(cm.__enter__)
    finally:
        await asyncio.to_thread(cm.__exit__, None, None, None)
