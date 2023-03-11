from fastapi import APIRouter
import interactive_prompts.routes.watch


router = APIRouter()
router.include_router(interactive_prompts.routes.watch.router)
