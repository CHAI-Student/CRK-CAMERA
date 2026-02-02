import asyncio
from typing import Literal, Optional


def ffmpeg_build_command_mjpeg(src: str, dst: str, width: int, height: int, fps: int) -> list[str]:
        # fmt: off
        ffmpeg_binary = [ "./ffmpeg-8.0/bin/ffmpeg" ]

        ffmpeg_options = [
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
        ]

        ffmpeg_input = [
            "-f", "rawvideo",
            "-framerate", f"{fps}",
            "-pixel_format", "yuyv422",
            "-video_size", f"{width}x{height}",
            "-color_range", "full",
            "-i", src,
        ]

        ffmpeg_output = [
            "-f", "avi",
            "-pixel_format", "yuv422p",
            "-codec:v", "mjpeg",
            "-qcomp:v", "1",
            "-qmin:v", "2",
            "-qmax:v", "5",
            "-color_range", "full",
            dst,
        ]
        # fmt: on

        command = (
            ffmpeg_binary
            + ffmpeg_options
            + ffmpeg_input
            + ffmpeg_output
        )

        return command

def ffmpeg_build_command_h264(src: str, dst: str, width: int, height: int, fps: int) -> list[str]:
        # fmt: off
        ffmpeg_binary = [ "./ffmpeg-8.0/bin/ffmpeg" ]

        ffmpeg_options = [
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
        ]

        ffmpeg_input = [
            "-f", "rawvideo",
            "-framerate", f"{fps}",
            "-pixel_format", "yuyv422",
            "-video_size", f"{width}x{height}",
            "-color_range", "full",
            "-i", src,
        ]

        ffmpeg_output = [
            "-f", "mp4",
            "-codec:v", "libx264",
            "-preset", "veryfast",
            "-pixel_format", "yuv420p",
            "-crf", "23",
            "-color_range", "full",
            dst,
        ]
        # fmt: on

        command = (
            ffmpeg_binary
            + ffmpeg_options
            + ffmpeg_input
            + ffmpeg_output
        )

        return command

async def ffmpeg_start(dst: str, width: int, height: int, fps: int, encoder: Literal["mjpeg", "h264"] = "mjpeg", log_path: Optional[str] = None) -> asyncio.subprocess.Process:
    if encoder == "mjpeg":
        command = ffmpeg_build_command_mjpeg(
            src="pipe:0",
            dst=dst,
            width=width,
            height=height,
            fps=fps,
        )
    elif encoder == "h264":
        command = ffmpeg_build_command_h264(
            src="pipe:0",
            dst=dst,
            width=width,
            height=height,
            fps=fps,
        )
    else:
        raise ValueError(f"Unsupported encoder: {encoder}")
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