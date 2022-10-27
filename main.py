from fastapi import FastAPI, WebSocket
import multiprocessing
import updater

multiprocessing.Process(target=updater.listen_forever_sync, daemon=True).start()
app = FastAPI(
    title="oseh websockets",
    description="hypersocial mindfulness app",
    version="1.0.0+alpha",
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
