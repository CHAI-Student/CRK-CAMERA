import asyncio
import logging

import pyudev

from utils.camera import CameraControl, run_camera
from utils.device import iter_capture_device_serials
from utils.ffmpeg import build_ffmpeg_command


logging.basicConfig(level=logging.INFO)


async def main():
    context = pyudev.Context()

    for serial in iter_capture_device_serials(context):
        dst = f"./{serial}.mp4"

        ffmpeg_command = build_ffmpeg_command(
            control=CameraControl(
                width=640,
                height=480,
                format="YUYV",
                fps=30,
            ),
            src="pipe:0",
            dst=dst,
        )

        process = await asyncio.create_subprocess_exec(
            *ffmpeg_command,
            stdin=asyncio.subprocess.PIPE,
        )

        try:
            assert process.stdin is not None
            frame_count = 0
            async for frame in run_camera(context, serial):
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
