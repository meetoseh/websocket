import asyncio
from fastapi import WebSocket
from loguru._logger import Logger as LoguruLogger
from starlette.websockets import WebSocketState


async def close(websocket: WebSocket, *, logger: LoguruLogger, timeout: float):
    """Attempts to cleanly close the given websocket"""
    try:
        logger.debug("close()")
        if websocket.state == WebSocketState.DISCONNECTED:
            logger.debug("already closed")
            return

        await asyncio.wait_for(websocket.close(), timeout=timeout)
        logger.debug("closed cleanly")
    except asyncio.TimeoutError:
        logger.debug("close() timed out")
