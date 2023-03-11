from fastapi import FastAPI, WebSocket
from starlette.middleware.cors import CORSMiddleware
from error_middleware import handle_request_error
import perpetual_pub_sub as pps
import interactive_prompts.router
import interactive_prompts.lib.meta
import updater
import asyncio
import os
from loguru import logger

app = FastAPI(
    title="oseh websockets",
    description="hypersocial mindfulness app",
    version="1.0.0+alpha",
    openapi_url="/api/2/openapi.json",
    docs_url="/api/2/docs-http",
    exception_handlers={Exception: handle_request_error},
)

if os.environ.get("ENVIRONMENT") == "dev":
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[os.environ["ROOT_FRONTEND_URL"]],
        allow_credentials=True,
        allow_methods=["GET", "POST", "HEAD", "PUT", "DELETE"],
        allow_headers=["Authorization"],
    )

app.include_router(
    interactive_prompts.router.router,
    prefix="/api/2/interactive_prompts",
    tags=["interactive_prompts"],
)
app.router.redirect_slashes = False


if pps.instance is None:
    pps.instance = pps.PerpetualPubSub()

background_tasks = set()


@app.on_event("startup")
def register_background_tasks():
    logger.add("websocket.log", enqueue=True, rotation="100 MB")

    background_tasks.add(asyncio.create_task(updater.listen_forever()))
    background_tasks.add(asyncio.create_task(pps.instance.run_in_background_async()))
    background_tasks.add(
        asyncio.create_task(
            interactive_prompts.lib.meta.purge_interactive_prompt_meta_loop()
        )
    )


@app.websocket("/api/2/test/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    while True:
        data = await websocket.receive_text()
        await websocket.send_text(f"Message text was: {data}")


@app.get("/")
def root():
    return {"message": "Hello World"}
