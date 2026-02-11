import asyncio
import logging

import pyudev

from utils.camera import CameraControl, run_camera
from utils.ffmpeg import build_ffmpeg_command

logging.basicConfig(level=logging.INFO)


async def main():
    context = pyudev.Context()

    dst = "./output.mp4"

    camera_control = CameraControl(
        width=640,
        height=480,
        format="YUYV",
        fps=30,
    )

    ffmpeg_command = build_ffmpeg_command(
        control=camera_control,
        src="pipe:0",
        dst=dst,
    )

    process = await asyncio.create_subprocess_exec(
        *ffmpeg_command,
        stdin=asyncio.subprocess.PIPE,
    )

    with open("./mapping.json", "r") as f:
        import json
        mapping: dict = json.load(f)
    
    serial = list(mapping)[0]

    try:
        assert process.stdin is not None
        frame_count = 0
        async for frame in run_camera(context, serial, control=camera_control):
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
    
    # print saved file size
    import os
    file_size = os.path.getsize(dst)
    print(f"Saved file size: {file_size / (1024 * 1024):.2f} MiB")


if __name__ == "__main__":
    asyncio.run(main())
