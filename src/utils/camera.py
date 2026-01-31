from typing import AsyncGenerator

import pyudev
from linuxpy.video.device import BufferType, Frame, VideoCapture

from utils.device import capture_device_from_serial


async def run_camera(
    ctx: pyudev.Context,
    serial: str,
    *,
    width: int = 640,
    height: int = 480,
    pixel_format: str = "YUYV",
    fps: int = 30,
) -> AsyncGenerator[Frame]:
    """
    Run the camera with the given serial number and yield frames.

    :param ctx: pyudev device database connection
    :type ctx: pyudev.Context
    :param serial: serial number of the camera device
    :type serial: str
    :param width: width of the video frame, defaults to 640
    :type width: int, optional
    :param height: height of the video frame, defaults to 480
    :type height: int, optional
    :param pixel_format: pixel format of the video frame, defaults to "YUYV"
    :type pixel_format: str, optional
    :param fps: frames per second, defaults to 30
    :type fps: int, optional

    :yield: Frames captured from the camera
    :rtype: AsyncGenerator[Frame]

    :raises DeviceNotFoundError: If the device with the given serial is not found
    :raises OSError: If there is an error accessing the device
    :raises asyncio.queues.QueueFull: If the internal queue is full
    """
    with capture_device_from_serial(ctx, serial) as device:
        device.set_format(BufferType.VIDEO_CAPTURE, width, height, pixel_format)
        device.set_fps(BufferType.VIDEO_CAPTURE, fps)
        with VideoCapture(device) as stream:
            async for frame in stream:
                yield frame
