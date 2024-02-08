from fastapi import APIRouter
import jobs_progress.routes.watch

router = APIRouter()
router.include_router(jobs_progress.routes.watch.router)
