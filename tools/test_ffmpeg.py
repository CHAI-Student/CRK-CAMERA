"""디버그용 스크립트: 카메라 1대 → ffmpeg 저장 경로를 단독 검증한다.

mapping.json의 첫 카메라에서 5초 분량 frame을 받아 ./output.mp4 로
저장하고 파일 크기를 출력한다.

실행: `uv run tools/test_ffmpeg.py` (프로젝트 루트에서)
"""

import asyncio
import json
import logging
import os
import sys
from pathlib import Path

# tools/에서 단독 실행할 수 있도록 src/를 import 경로에 추가한다.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pyudev

from utils.camera import CameraControl, run_camera
from utils.ffmpeg import build_ffmpeg_command

logging.basicConfig(level=logging.INFO)


async def main():
    context = pyudev.Context()

    dst = "./output.mp4"

    # 시스템 공통 고정 해상도 640x480 (main.py의 CameraControl과 동일)
    camera_control = CameraControl(
        width=640,
        height=480,
        format="YUYV",
        fps=30,
    )

    # YUYV raw 입력 → h264(MP4) 인코딩
    ffmpeg_command = build_ffmpeg_command(
        camera_control.format,
        camera_control.width,
        camera_control.height,
        camera_control.fps,
        src="pipe:0",
        dst=dst,
        encoder="h264",
    )

    process = await asyncio.create_subprocess_exec(
        *ffmpeg_command,
        stdin=asyncio.subprocess.PIPE,
    )

    # mapping.json은 {device: {serial, index}, mapping: {index}} 객체의 list
    with open("./mapping.json", "r") as f:
        mapping: list = json.load(f)

    device = mapping[0]["device"]

    try:
        assert process.stdin is not None
        frame_count = 0
        async for frame in run_camera(context, device["serial"], device["index"], control=camera_control):
            process.stdin.write(frame.data)
            await process.stdin.drain()
            frame_count += 1
            if frame_count >= 30 * 5:
                break
    finally:
        if process.stdin is not None:
            process.stdin.close()
            await process.stdin.wait_closed()
        await process.wait()

    # 저장된 파일 크기 출력
    file_size = os.path.getsize(dst)
    print(f"Saved file size: {file_size / (1024 * 1024):.2f} MiB")


if __name__ == "__main__":
    asyncio.run(main())
