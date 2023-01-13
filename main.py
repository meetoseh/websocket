from fastapi import FastAPI, WebSocket
from starlette.middleware.cors import CORSMiddleware
from error_middleware import handle_request_error
import perpetual_pub_sub as pps
import journeys.router
import journeys.lib.meta
import updater
import asyncio
import os

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

app.include_router(journeys.router.router, prefix="/api/2/journeys", tags=["journeys"])
app.router.redirect_slashes = False


if pps.instance is None:
    pps.instance = pps.PerpetualPubSub()

background_tasks = set()


@app.on_event("startup")
def register_background_tasks():
    background_tasks.add(asyncio.create_task(updater.listen_forever()))
    background_tasks.add(
        asyncio.create_task(journeys.lib.meta.purge_journey_meta_loop())
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
