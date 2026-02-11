import asyncio
from fastapi import APIRouter, Request
from fastapi.responses import Response

from services.capture import CaptureFrame, CaptureService

router = APIRouter(prefix="/test", tags=["test"])

@router.get("/frame")
async def get_frame(request: Request, index: int = 0):
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
            # Convert YUYV to JPEG
            import cv2
            import numpy as np

            yuyv_image = np.frombuffer(frame.data, dtype=np.uint8)
            yuyv_image = yuyv_image.reshape((480, 640, 2))  # Assuming 640x480 resolution
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
        