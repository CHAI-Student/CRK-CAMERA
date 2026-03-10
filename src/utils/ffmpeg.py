import asyncio
from typing import Literal, Optional

from utils.camera import CameraControl

ffmpeg_binary = [ "ffmpeg" ]

ffmpeg_options = [
    "-hide_banner",
    "-loglevel", "error",
    "-y",
]

def build_ffmpeg_input_argument(control: CameraControl, src: str) -> list[str]:
    if control.format.upper() == "YUYV":
        return [
            "-f", "rawvideo",
            "-video_size", f"{control.width}x{control.height}",
            "-pixel_format", "yuyv422",
            "-framerate", f"{control.fps}",
            "-i", src,
        ]
    elif control.format.upper() == "MJPG":
        return [
            "-f", "image2pipe",
            "-video_size", f"{control.width}x{control.height}",
            "-codec:v", "mjpeg",
            "-framerate", f"{control.fps}",
            "-i", src,
        ]
    else:
        raise ValueError(f"Unsupported format: {control.format}")

def build_ffmpeg_output_argument(control: CameraControl, dst: str, encoder: Literal["mjpeg", "h264"]) -> list[str]:
    if encoder == "mjpeg":
        if control.format.upper() == "YUYV":
            return [
                "-f", "avi",
                "-pixel_format", "yuv422p",
                "-codec:v", "mjpeg",
                "-qcomp:v", "1",
                "-qmin:v", "2",
                "-qmax:v", "4",
                dst,
            ]
        elif control.format.upper() == "MJPG":
            return [
                "-f", "avi",
                "-codec:v", "copy",
                dst,
            ]
        else:
            raise ValueError(f"Unsupported format for mjpeg encoder: {control.format}")
    elif encoder == "h264":
        return [
            "-f", "mp4",
            "-pixel_format", "yuv420p",
            "-codec:v", "libx264",
            "-preset", "veryfast",
            "-crf", "23",
            dst,
        ]
    else:
        raise ValueError(f"Unsupported encoder: {encoder}")

def build_ffmpeg_command(control: CameraControl, src: str, dst: str, encoder: Literal["mjpeg", "h264"] = "mjpeg") -> list[str]:
    ffmpeg_input = build_ffmpeg_input_argument(control, src)
    ffmpeg_output = build_ffmpeg_output_argument(control, dst, encoder)
    command = (
        ffmpeg_binary
        + ffmpeg_options
        + ffmpeg_input
        + ffmpeg_output
    )
    return command

async def ffmpeg_start(control: CameraControl, dst: str, encoder: Literal["mjpeg", "h264"] = "mjpeg", log_path: Optional[str] = None) -> asyncio.subprocess.Process:
    command = build_ffmpeg_command(control, src="pipe:0", dst=dst, encoder=encoder)

    process = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.DEVNULL if log_path is None else open(log_path, "a"),
        stderr=asyncio.subprocess.DEVNULL if log_path is None else open(log_path, "a"),
    )
    return process

async def ffmpeg_stop(process: asyncio.subprocess.Process):
    if process.stdin is not None:
        process.stdin.close()
        await process.stdin.wait_closed()
    await process.wait()

async def ffmpeg_feed_data(process: asyncio.subprocess.Process, frame: bytes):
    assert process.stdin is not None
    process.stdin.write(frame)
    await process.stdin.drain()
