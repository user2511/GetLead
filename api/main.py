import os
import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from api.health import router as health_router
from api.webhooks import router as webhook_router
from services.db_service import init_db
from services.scheduler_service import start_scheduler, stop_scheduler

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="LeadFlow API",
    description="Multi-agent local business automation platform",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router, tags=["Health"])
app.include_router(webhook_router, prefix="/webhook", tags=["Webhooks"])

@app.on_event("startup")
async def startup():
    logger.info("LeadFlow starting up...")
    init_db()
    start_scheduler()
    logger.info("LeadFlow ready — all systems go")

@app.on_event("shutdown")
async def shutdown():
    stop_scheduler()
    logger.info("LeadFlow shut down cleanly")