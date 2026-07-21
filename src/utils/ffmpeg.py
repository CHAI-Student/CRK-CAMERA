"""ffmpeg 명령 구성 및 subprocess 제어 유틸리티.

stdin(pipe)으로 raw frame을 받아 파일로 인코딩하는 ffmpeg 명령을
조립하고, 프로세스 시작/frame 공급/종료를 담당한다.

- 입력: YUYV(rawvideo) 또는 MJPG(image2pipe)
- 출력: mjpeg(AVI, MJPG 입력이면 재인코딩 없이 copy) 또는 h264(MP4)
"""

import asyncio
from typing import Literal, Optional

from utils.camera import CameraControl

ffmpeg_binary = [ "ffmpeg" ]

# 공통 옵션: 배너 숨김, error 로그만 출력, 출력 파일 덮어쓰기 허용
ffmpeg_options = [
    "-hide_banner",
    "-loglevel", "error",
    "-y",
]

def build_ffmpeg_input_argument(format: str, width: int, height: int, fps: int, src: str) -> list[str]:
    """픽셀 포맷에 맞는 ffmpeg 입력 인자 목록을 만든다."""
    if format.upper() == "YUYV":
        return [
            "-f", "rawvideo",
            "-video_size", f"{width}x{height}",
            "-pixel_format", "yuyv422",
            "-framerate", f"{fps}",
            "-i", src,
        ]
    elif format.upper() == "MJPG":
        return [
            "-f", "image2pipe",
            "-video_size", f"{width}x{height}",
            "-codec:v", "mjpeg",
            "-framerate", f"{fps}",
            "-i", src,
        ]
    else:
        raise ValueError(f"Unsupported format: {format}")

def build_ffmpeg_output_argument(format: str, width: int, height: int, fps: int, dst: str, encoder: Literal["mjpeg", "h264"]) -> list[str]:
    """encoder 종류에 맞는 ffmpeg 출력 인자 목록을 만든다.

    mjpeg encoder에 MJPG 입력이면 재인코딩 없이 stream copy한다.
    """
    if encoder == "mjpeg":
        if format.upper() == "YUYV":
            return [
                "-f", "avi",
                "-pixel_format", "yuv422p",
                "-codec:v", "mjpeg",
                "-qcomp:v", "1",
                "-qmin:v", "2",
                "-qmax:v", "4",
                "-framerate", f"{fps}",
                dst,
            ]
        elif format.upper() == "MJPG":
            return [
                "-f", "avi",
                "-codec:v", "copy",
                "-framerate", f"{fps}",
                dst,
            ]
        else:
            raise ValueError(f"Unsupported format for mjpeg encoder: {format}")
    elif encoder == "h264":
        return [
            "-f", "mp4",
            "-pixel_format", "yuv420p",
            "-codec:v", "libx264",
            "-preset", "veryfast",
            "-crf", "23",
            "-framerate", f"{fps}",
            dst,
        ]
    else:
        raise ValueError(f"Unsupported encoder: {encoder}")

def build_ffmpeg_command(format: str, width: int, height: int, fps: int, src: str, dst: str, encoder: Literal["mjpeg", "h264"] = "mjpeg") -> list[str]:
    """입력/출력 인자를 합쳐 완전한 ffmpeg 명령을 만든다."""
    ffmpeg_input = build_ffmpeg_input_argument(format, width, height, fps, src)
    ffmpeg_output = build_ffmpeg_output_argument(format, width, height, fps, dst, encoder)
    command = (
        ffmpeg_binary
        + ffmpeg_options
        + ffmpeg_input
        + ffmpeg_output
    )
    return command

async def ffmpeg_start(control: CameraControl, dst: str, encoder: Literal["mjpeg", "h264"] = "mjpeg", log_path: Optional[str] = None) -> asyncio.subprocess.Process:
    """stdin으로 frame을 받는 ffmpeg 프로세스를 시작한다.

    :param control: 입력 frame의 카메라 설정 (포맷/해상도/fps)
    :param dst: 출력 파일 경로
    :param encoder: 출력 encoder ("mjpeg" 또는 "h264")
    :param log_path: 지정하면 ffmpeg의 stdout/stderr를 해당 파일에 append
    """
    command = build_ffmpeg_command(control.format, control.width, control.height, control.fps, src="pipe:0", dst=dst, encoder=encoder)

    if log_path is None:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
    else:
        # 로그 파일은 subprocess에 fd가 복제된 뒤 부모 쪽을 즉시 닫는다.
        # (기존에는 open()한 파일 객체를 닫지 않아 trigger가 잦은 장기 운용에서
        #  fd가 누적될 수 있었음. append 모드라 stdout/stderr가 fd를 공유해도 안전)
        log_file = open(log_path, "a")
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.PIPE,
                stdout=log_file,
                stderr=log_file,
            )
        finally:
            log_file.close()
    return process

async def ffmpeg_stop(process: asyncio.subprocess.Process):
    """stdin을 닫아 ffmpeg가 파일을 마무리하고 종료할 때까지 기다린다."""
    if process.stdin is not None:
        process.stdin.close()
        await process.stdin.wait_closed()
    await process.wait()

async def ffmpeg_feed_data(process: asyncio.subprocess.Process, frame: bytes):
    """frame 1장을 ffmpeg stdin에 쓰고 drain될 때까지 기다린다."""
    assert process.stdin is not None
    process.stdin.write(frame)
    await process.stdin.drain()
