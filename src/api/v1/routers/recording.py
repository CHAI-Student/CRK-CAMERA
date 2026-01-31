from fastapi import APIRouter

router = APIRouter(prefix="/recording", tags=["recording"])

@router.get("/start")
async def start_recording():
    return {"status": "recording started"}

@router.get("/stop")
async def stop_recording():
    return {"status": "recording stopped"}
