from fastapi import FastAPI, WebSocket
from starlette.middleware.cors import CORSMiddleware
from error_middleware import handle_request_error
from lifespan import lifespan_handler
from mp_helper import adapt_threading_event_to_asyncio
import perpetual_pub_sub
import interactive_prompts.router
import jobs_progress.router
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
app.include_router(
    jobs_progress.router.router,
    prefix="/api/2/jobs",
    tags=["jobs"],
)
app.router.redirect_slashes = False


background_tasks = set()


@lifespan_handler
async def register_background_tasks():
    if perpetual_pub_sub.instance is None:
        perpetual_pub_sub.instance = perpetual_pub_sub.PerpetualPubSub()

    logger.add("websocket.log", enqueue=True, rotation="100 MB")

    background_tasks.add(asyncio.create_task(updater.listen_forever()))
    background_tasks.add(
        asyncio.create_task(perpetual_pub_sub.instance.run_in_background_async())
    )
    background_tasks.add(
        asyncio.create_task(
            interactive_prompts.lib.meta.purge_interactive_prompt_meta_loop()
        )
    )
    yield
    perpetual_pub_sub.instance.exit_event.set()

    await adapt_threading_event_to_asyncio(
        perpetual_pub_sub.instance.exitted_event
    ).wait()


@app.websocket("/api/2/test/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    while True:
        data = await websocket.receive_text()
        await websocket.send_text(f"Message text was: {data}")


@app.get("/")
def root():
    return {"message": "Hello World"}
