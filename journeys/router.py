from fastapi import APIRouter
import journeys.routes.watch


router = APIRouter()
router.include_router(journeys.routes.watch.router)
