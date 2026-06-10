from fastapi import APIRouter
from datetime import datetime

router = APIRouter()

@router.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "service": "LeadFlow API",
        "timestamp": datetime.now().isoformat(),
        "version": "1.0.0"
    }