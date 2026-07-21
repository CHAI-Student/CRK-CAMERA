"""Jetson 하드웨어 JPEG 디코더(gst-nvjpegdec) 연동 pipeline 유틸리티.

MJPG frame을 tools/gst-nvjpegdec 바이너리(GStreamer nvjpegdec)로 하드웨어
디코딩한 뒤 ffmpeg h264 인코딩으로 잇는 2단 pipeline을 구성한다.
YUYV 입력이면 디코딩이 필요 없으므로 cat으로 그대로 통과시킨다.

참고: 현재 애플리케이션 코드에서는 사용되지 않는 실험용 유틸리티이다.
"""

import asyncio
import os
from typing import Optional

from utils.camera import CameraControl
from utils.ffmpeg import build_ffmpeg_command

gst_nvjpegdec_binary = [ "./tools/gst-nvjpegdec/main" ]

async def gst_nvjpegdec_start() -> asyncio.subprocess.Process:
    """gst-nvjpegdec 프로세스를 단독(stdin/stdout pipe)으로 시작한다."""
    process = await asyncio.create_subprocess_exec(
        *gst_nvjpegdec_binary,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
    )
    return process

async def gst_pipeline_start(control: CameraControl, dst: str, log_path: Optional[str] = None) -> tuple[asyncio.subprocess.Process, asyncio.subprocess.Process]:
    """디코더 프로세스와 ffmpeg 프로세스를 OS pipe로 연결해 시작한다.

    :return: (gst_nvjpegdec 프로세스, ffmpeg 프로세스)
    """
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
    """pipeline을 앞단(디코더)부터 순서대로 닫고 두 프로세스의 종료를 기다린다."""
    if gst_nvjpegdec_process.stdin is not None:
        gst_nvjpegdec_process.stdin.close()
        await gst_nvjpegdec_process.stdin.wait_closed()
    await gst_nvjpegdec_process.wait()

    if ffmpeg_process.stdin is not None:
        ffmpeg_process.stdin.close()
        await ffmpeg_process.stdin.wait_closed()
    await ffmpeg_process.wait()

async def gst_pipeline_feed_data(gst_nvjpegdec_process: asyncio.subprocess.Process, frame: bytes):
    """frame 1장을 pipeline 앞단(디코더) stdin에 쓰고 drain될 때까지 기다린다."""
    assert gst_nvjpegdec_process.stdin is not None
    gst_nvjpegdec_process.stdin.write(frame)
    await gst_nvjpegdec_process.stdin.drain()