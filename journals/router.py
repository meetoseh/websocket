from fastapi import APIRouter
import journals.routes.chat

router = APIRouter()
router.include_router(journals.routes.chat.router)
