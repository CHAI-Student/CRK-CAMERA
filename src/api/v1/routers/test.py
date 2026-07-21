"""테스트/디버그용 API 라우터.

카메라 영상을 눈으로 확인하기 위한 엔드포인트를 제공한다.

- GET /test/frame: 단일 frame을 JPEG로 반환
- GET /test/stream: MJPEG 스트리밍 (multipart/x-mixed-replace)
- GET /test/streams: 모든 카메라 스트림을 모아 보는 HTML 페이지
"""

import asyncio
from fastapi import APIRouter, Request
from fastapi.responses import Response, HTMLResponse, StreamingResponse

from services.capture import CaptureFrame, CaptureService

router = APIRouter(prefix="/test", tags=["test"])

@router.get("/frame")
async def get_frame(request: Request, index: int = 0):
    """지정한 카메라의 현재 frame 1장을 JPEG 이미지로 반환한다."""
    capture_services: dict[int, CaptureService] = request.app.state.capture_services

    capture_service = capture_services.get(index)
    if capture_service is None:
        raise ValueError(f"Capture service for index {index} not found")
    
    frame_queue: asyncio.Queue[CaptureFrame] = asyncio.Queue(maxsize=1)

    await capture_service.subscribe(frame_queue)
    try:
        frame = await frame_queue.get()
        frame_queue.shutdown()
        await capture_service.unsubscribe(frame_queue)

        if frame.pixel_format == "JPEG" or frame.pixel_format == "MJPEG":
            return Response(content=frame.data, media_type="image/jpeg")
        elif frame.pixel_format == "YUYV":
            # YUYV → JPEG 변환
            import cv2
            import numpy as np

            yuyv_image = np.frombuffer(frame.data, dtype=np.uint8)
            yuyv_image = yuyv_image.reshape((480, 640, 2))  # 시스템 공통 고정 해상도 640x480 (main.py CameraControl과 동일)
            bgr_image = cv2.cvtColor(yuyv_image, cv2.COLOR_YUV2BGR_YUYV)
            ret, jpeg_image = cv2.imencode('.jpg', bgr_image)
            if not ret:
                raise ValueError("Failed to encode YUYV image to JPEG")
            return Response(content=jpeg_image.tobytes(), media_type="image/jpeg")
        else:
            raise ValueError(f"Unsupported pixel format: {frame.pixel_format}") 
    except:
        await capture_service.unsubscribe(frame_queue)
        raise

async def generate_frames(capture_service: CaptureService):
    """카메라를 구독해 frame들을 MJPEG multipart 조각으로 yield한다."""
    frame_queue: asyncio.Queue[CaptureFrame] = asyncio.Queue(maxsize=30)

    try:
        await capture_service.subscribe(frame_queue)

        while True:
            frame = await frame_queue.get()

            if frame.pixel_format == "JPEG" or frame.pixel_format == "MJPEG":
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + frame.data + b'\r\n')
            elif frame.pixel_format == "YUYV":
                # YUYV → JPEG 변환
                import cv2
                import numpy as np

                yuyv_image = np.frombuffer(frame.data, dtype=np.uint8)
                yuyv_image = yuyv_image.reshape((480, 640, 2))  # 시스템 공통 고정 해상도 640x480 (main.py CameraControl과 동일)
                bgr_image = cv2.cvtColor(yuyv_image, cv2.COLOR_YUV2BGR_YUYV)
                ret, jpeg_image = cv2.imencode('.jpg', bgr_image)
                if not ret:
                    raise ValueError("Failed to encode YUYV image to JPEG")
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + jpeg_image.tobytes() + b'\r\n')
            else:
                raise ValueError(f"Unsupported pixel format: {frame.pixel_format}") 

    finally:
        frame_queue.shutdown()
        await capture_service.unsubscribe(frame_queue)
        
@router.get("/stream")
async def video_feed(request: Request, index: int = 0):
    """지정한 카메라의 MJPEG 스트림을 반환한다."""
    capture_services: dict[int, CaptureService] = request.app.state.capture_services

    capture_service = capture_services.get(index)
    if capture_service is None:
        raise ValueError(f"Capture service for index {index} not found")
    
    return StreamingResponse(
        generate_frames(capture_service), 
        media_type="multipart/x-mixed-replace; boundary=frame"
    )

@router.get("/streams", response_class=HTMLResponse)
async def videos_feed(request: Request):
    """모든 카메라의 스트림을 3열 표로 보여주는 HTML 페이지를 반환한다."""
    capture_services: list[int] = sorted(list(request.app.state.capture_services))

    camera_mapping: dict[int, str] = { entry["mapping"]["index"]: entry["device"]["serial"] for entry in request.app.state.camera_mapping }

    rows = []

    rows.append("<tr>")
    for i, index in enumerate(capture_services):
        rows.append(f"""
                <td>
                    <img src="/test/stream?index={index}" />
                    <p>{camera_mapping[index]}</p>
                </td>
        """)
        if i % 3 == 2:
            rows.append("</tr><tr>")
    rows.append("</tr>")

    rows = ''.join(rows)

    return """
    <html>
        <head>
            <title>Streams</title>
        </head>
        <body>
            <table>
    """ + rows + """
            </table>
        </body>
    </html>
    """
