import asyncio
import os
from typing import Literal, Optional

from utils.camera import CameraControl
from utils.ffmpeg import build_ffmpeg_command

gst_nvjpegdec_binary = [ "./tools/gst-nvjpegdec/main" ]

async def gst_nvjpegdec_start() -> asyncio.subprocess.Process:
    process = await asyncio.create_subprocess_exec(
        *gst_nvjpegdec_binary,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
    )
    return process

async def gst_pipeline_start(control: CameraControl, dst: str, log_path: Optional[str] = None) -> tuple[asyncio.subprocess.Process, asyncio.subprocess.Process]:
    r, w = os.pipe()

    if control.format.upper() == "MJPG":
        gst_nvjpegdec_process = await asyncio.create_subprocess_exec(
            *gst_nvjpegdec_binary,
            stdin=asyncio.subprocess.PIPE,
            stdout=w,
        )
    elif control.format.upper() == "YUYV":
        gst_nvjpegdec_process = await asyncio.create_subprocess_exec(
            "cat",
            stdin=asyncio.subprocess.PIPE,
            stdout=w,
        )
    else:
        raise ValueError(f"Unsupported format: {control.format}")

    os.close(w)

    ffmpeg_process = await asyncio.create_subprocess_exec(
        *build_ffmpeg_command("YUYV", control.width, control.height, control.fps, src="pipe:0", dst=dst, encoder="h264"),
        stdin=r,
        stdout=asyncio.subprocess.DEVNULL if log_path is None else open(log_path, "a"),
        stderr=asyncio.subprocess.DEVNULL if log_path is None else open(log_path, "a"),
    )

    os.close(r)

    return gst_nvjpegdec_process, ffmpeg_process

async def gst_pipeline_stop(gst_nvjpegdec_process: asyncio.subprocess.Process, ffmpeg_process: asyncio.subprocess.Process):
    if gst_nvjpegdec_process.stdin is not None:
        gst_nvjpegdec_process.stdin.close()
        await gst_nvjpegdec_process.stdin.wait_closed()
    await gst_nvjpegdec_process.wait()

    if ffmpeg_process.stdin is not None:
        ffmpeg_process.stdin.close()
        await ffmpeg_process.stdin.wait_closed()
    await ffmpeg_process.wait()

async def gst_pipeline_feed_data(gst_nvjpegdec_process: asyncio.subprocess.Process, frame: bytes):
    assert gst_nvjpegdec_process.stdin is not None
    gst_nvjpegdec_process.stdin.write(frame)
    await gst_nvjpegdec_process.stdin.drain()