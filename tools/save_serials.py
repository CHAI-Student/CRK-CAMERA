"""디버그용 스크립트: 연결된 모든 카메라에서 5초씩 영상을 저장해 본다.

카메라별로 ffmpeg를 띄워 ./<serial>.mp4 로 저장하고 파일 크기를 출력한다.
mapping.json 없이도 연결된 장치 전부를 순회하므로, 신규 장비에서 카메라
동작을 빠르게 확인할 때 사용한다.

실행: `uv run tools/save_serials.py` (프로젝트 루트에서)
"""

import asyncio
import logging
import os
import sys
from pathlib import Path

# tools/에서 단독 실행할 수 있도록 src/를 import 경로에 추가한다.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pyudev

from utils.camera import CameraControl, run_camera
from utils.device import iter_capture_device_serials
from utils.ffmpeg import build_ffmpeg_command

logging.basicConfig(level=logging.INFO)


async def main():
    context = pyudev.Context()

    # 시스템 공통 고정 해상도 640x480 (main.py의 CameraControl과 동일)
    camera_control = CameraControl(
        width=640,
        height=480,
        format="YUYV",
        fps=30,
    )

    # serial 중복(캡처 노드가 여러 개인 장치) 시 첫 노드만 시도한다.
    for serial in dict.fromkeys(iter_capture_device_serials(context)):
        dst = f"./{serial}.mp4"

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

        try:
            assert process.stdin is not None
            frame_count = 0
            async for frame in run_camera(context, serial, index=0, control=camera_control):
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
