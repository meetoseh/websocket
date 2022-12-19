from fastapi import FastAPI, WebSocket
from starlette.middleware.cors import CORSMiddleware
from error_middleware import handle_request_error
import perpetual_pub_sub as pps
import journeys.router
import updater
import asyncio
import os

if pps.instance is None:
    pps.instance = pps.PerpetualPubSub()

asyncio.ensure_future(updater.listen_forever())
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


@app.websocket("/api/2/test/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    while True:
        data = await websocket.receive_text()
        await websocket.send_text(f"Message text was: {data}")


@app.get("/")
def root():
    return {"message": "Hello World"}
