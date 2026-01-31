from fastapi import APIRouter

router = APIRouter(tags=["management"])

@router.get("/health")
async def get_health():
    return {"status": "ok"}