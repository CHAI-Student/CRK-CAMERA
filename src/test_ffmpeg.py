import asyncio
import logging

import pyudev

from utils.camera import run_camera
from utils.ffmpeg import ffmpeg_build_command


logging.basicConfig(level=logging.INFO)


async def main():
    context = pyudev.Context()

    ffmpeg_command = ffmpeg_build_command(
        src="pipe:0",
        dst="./output.avi",
        width=640,
        height=480,
        fps=30,
    )

    process = await asyncio.create_subprocess_exec(
        *ffmpeg_command,
        stdin=asyncio.subprocess.PIPE,
    )

    with open("./mapping.json", "r") as f:
        import json
        mapping: dict = json.load(f)
    
    serial = list(mapping)[5]

    try:
        assert process.stdin is not None
        frame_count = 0
        async for frame in run_camera(context, serial):
            process.stdin.write(frame.data)
            await process.stdin.drain()
            frame_count += 1
            if frame_count >= 30 * 10:
                break
    finally:
        if process.stdin is not None:
            process.stdin.close()
        await process.wait()
    
    # print saved file size
    import os
    file_size = os.path.getsize("./output.avi")
    print(f"Saved file size: {file_size / (1024 * 1024):.2f} MiB")


if __name__ == "__main__":
    asyncio.run(main())
