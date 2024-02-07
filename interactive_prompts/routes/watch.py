import time
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, WebSocket
from itgs import Itgs
from interactive_prompts.auth import auth_any
from image_files.auth import create_jwt as create_image_file_jwt
from pydantic import BaseModel, Field
from interactive_prompts.lib.data import (
    InteractivePromptWatchCoreData,
    InteractivePromptWatchData,
    InteractivePromptWatchLatencyDetectionData,
)
import asyncio
import loguru
from loguru._logger import Logger as LoguruLogger
from pydantic import ValidationError
from interactive_prompts.lib.meta import get_interactive_prompt_meta
from interactive_prompts.lib.packets import (
    AUTH_REQUEST_RESPONSE_ERROR_TYPES,
    SYNC_RESPONSE_RESPONSE_ERROR_TYPES,
    AuthRequestPacket,
    AuthRequestResponseErrorPacket,
    AuthResponseSuccessPacket,
    AuthResponseSuccessPacketData,
    ErrorPacketData,
    EventBatchPacket,
    EventBatchPacketData,
    EventBatchPacketDataItem,
    ImageRef,
    SyncRequestPacket,
    SyncRequestPacketData,
    SyncResponsePacket,
    SyncResponseResponseErrorPacket,
)
import secrets
from starlette.websockets import WebSocketState


router = APIRouter()


CONNECT_TIMEOUT: float = 20.0
"""How long the client has to finish setting up the connection before it is
terminated (i.e., socket.accept())
"""

RECEIVE_TIMEOUT: float = 5.0
"""When the client is connected and we are expecting a packet from them, how
long we wait before we assume they have disconnected.
"""

SEND_TIMEOUT: float = 1.0
"""When we are trying to send a packet to the client, how long before we wait
to queue the packet to be sent. This is not the time required for the client to
receive the packet unless the tcp buffer is full.
"""

CLOSE_TIMEOUT: float = 5.0
"""When we are trying to cleanly shutdown the websocket, how long we wait before
we give up
"""

LATENCY_DETECTION_INTERVAL: float = 2.0
"""Seconds between latency detection packets"""

EVENT_BATCH_INTERVAL: float = 0.25
"""After receiving an event, we wait this duration before sending it and any other
events we received during that interval. This can be a significant throughput boost
if there are a lot of events, but it also sets a minimum latency for events to be
received by the client.
"""

STAY_PAST_CLOSE_TIME: float = 10.0
"""The maximum time in seconds the client can stay connected after the end of the
prompt before we disconnect them.
"""

assert (
    LATENCY_DETECTION_INTERVAL > SEND_TIMEOUT
), "latency detection interval must be greater than send timeout"

assert STAY_PAST_CLOSE_TIME > max(
    SEND_TIMEOUT, RECEIVE_TIMEOUT
), "STAY_PAST_CLOSE_TIME must be greater than SEND_TIMEOUT and RECEIVE_TIMEOUT"


@router.websocket("/live")
async def watch_interactive_prompt(websocket: WebSocket):
    """See docs/routes/interactive_prompts/live.md"""
    logger = loguru.logger.patch(
        lambda record: record.update(
            {
                "message": f"interactive_prompts.live {id(websocket)=} {record['message']}"
            }
        )
    )

    try:
        logger.debug("accept()")
        await asyncio.wait_for(websocket.accept(), timeout=CONNECT_TIMEOUT)
    except asyncio.TimeoutError:
        logger.debug("accept() timed out")
        await close(websocket, logger=logger)
        return

    async with Itgs() as itgs:
        data = await handle_initial_handshake(websocket, itgs=itgs, logger=logger)
        if not data:
            return

        await handle_stream(websocket, itgs=itgs, logger=logger, data=data)


async def handle_initial_handshake(
    websocket: WebSocket, *, itgs: Itgs, logger: LoguruLogger
) -> Optional[InteractivePromptWatchData]:
    """Handles the initial handshake between the client and server."""

    try:
        logger.debug("receiving AuthRequest")
        raw = await asyncio.wait_for(websocket.receive(), timeout=RECEIVE_TIMEOUT)
    except asyncio.TimeoutError:
        logger.debug("receiving AuthRequest timed out")
        await close(websocket, logger=logger)
        return

    if raw["type"] == "websocket.disconnect":
        logger.debug("client cleanly disconnected before sending AuthRequest")
        return

    try:
        parsed = AuthRequestPacket.parse_raw(
            raw["bytes"] if raw.get("bytes") else raw["text"],
            content_type="application/json",
        )
    except ValidationError as e:
        uid = gen_uid()
        logger.exception("uid={uid} received unparseable AuthRequest", uid=repr(uid))
        try:
            await asyncio.wait_for(
                websocket.send_text(
                    AuthRequestResponseErrorPacket(
                        success=False,
                        type="error",
                        uid=uid,
                        data=ErrorPacketData[AUTH_REQUEST_RESPONSE_ERROR_TYPES](
                            code=422, type="unprocessable_entity", message=str(e)
                        ),
                    ).model_dump_json()
                ),
                timeout=SEND_TIMEOUT,
            )
        except asyncio.TimeoutError:
            logger.debug("sending AuthRequestResponseErrorPacket timed out")

        await close(websocket, logger=logger)
        return

    auth_result = await auth_any(itgs, authorization=f"bearer {parsed.data.jwt}")
    if (
        not auth_result.success
        or auth_result.result.interactive_prompt_uid
        != parsed.data.interactive_prompt_uid
    ):
        uid = gen_uid()
        logger.debug(
            "uid={uid} received AuthRequest with invalid JWT: jwt={jwt} (success={success}, requested interactive_prompt_uid={interactive_prompt_uid})",
            uid=repr(uid),
            jwt=repr(parsed.data.jwt),
            success=repr(auth_result.success),
            interactive_prompt_uid=repr(parsed.data.interactive_prompt_uid),
        )
        try:
            await asyncio.wait_for(
                websocket.send_text(
                    AuthRequestResponseErrorPacket(
                        success=False,
                        type="error",
                        uid=uid,
                        data=ErrorPacketData[AUTH_REQUEST_RESPONSE_ERROR_TYPES](
                            code=403, type="forbidden", message="invalid JWT"
                        ),
                    ).model_dump_json()
                ),
                timeout=SEND_TIMEOUT,
            )
        except asyncio.TimeoutError:
            logger.debug("sending AuthRequestResponseErrorPacket timed out")

        await close(websocket, logger=logger)
        return

    bandwidth = parsed.data.bandwidth
    lookback = parsed.data.lookback
    lookahead = parsed.data.lookahead
    meta = await get_interactive_prompt_meta(itgs, parsed.data.interactive_prompt_uid)
    if meta is None:
        uid = gen_uid()
        logger.warning(
            "uid={uid} received AuthRequest with valid JWT, but interactive_prompt_uid={interactive_prompt_uid} does not exist",
            uid=repr(uid),
            interactive_prompt_uid=repr(parsed.data.interactive_prompt_uid),
        )
        try:
            await asyncio.wait_for(
                websocket.send_text(
                    AuthRequestResponseErrorPacket(
                        success=False,
                        type="error",
                        uid=uid,
                        data=ErrorPacketData[AUTH_REQUEST_RESPONSE_ERROR_TYPES](
                            code=404,
                            type="not_found",
                            message="the interactive prompt does not exist; it may have been deleted",
                        ),
                    ).model_dump_json()
                ),
                timeout=SEND_TIMEOUT,
            )
        except asyncio.TimeoutError:
            logger.debug("sending AuthRequestResponseErrorPacket timed out")

        await close(websocket, logger=logger)
        return

    uid = gen_uid()
    response = SyncRequestPacket(
        success=True,
        type="sync_request",
        uid=uid,
        data=SyncRequestPacketData(),
    ).model_dump_json()

    logger.debug(
        "uid={uid} sending SyncRequest at about now={now}",
        uid=repr(uid),
        now=repr(time.perf_counter()),
    )
    originate_timestamp = time.perf_counter()
    try:
        await asyncio.wait_for(websocket.send_text(response), timeout=SEND_TIMEOUT)
    except asyncio.TimeoutError:
        logger.debug("sending SyncRequest timed out")
        await close(websocket, logger=logger)
        return

    try:
        logger.debug("receiving SyncResponse")
        raw = await asyncio.wait_for(websocket.receive(), timeout=RECEIVE_TIMEOUT)
    except asyncio.TimeoutError:
        logger.debug("receiving SyncResponse timed out")
        await close(websocket, logger=logger)
        return

    destination_timestamp = time.perf_counter()

    if raw["type"] == "websocket.disconnect":
        logger.debug("client cleanly disconnected before sending SyncResponse")
        return

    try:
        parsed = SyncResponsePacket.parse_raw(
            raw["bytes"] if raw.get("bytes") else raw["text"],
            content_type="application/json",
        )
    except ValidationError as e:
        uid = gen_uid()
        logger.exception("uid={uid} received unparseable SyncResponse", uid=repr(uid))
        try:
            await asyncio.wait_for(
                websocket.send_text(
                    SyncResponseResponseErrorPacket(
                        success=False,
                        type="error",
                        uid=uid,
                        data=ErrorPacketData[SYNC_RESPONSE_RESPONSE_ERROR_TYPES](
                            code=422, type="unprocessable_entity", message=str(e)
                        ),
                    ).model_dump_json()
                ),
                timeout=SEND_TIMEOUT,
            )
        except asyncio.TimeoutError:
            logger.debug("sending SyncResponseErrorPacket timed out")

        await close(websocket, logger=logger)
        return

    prompt_time_perf_counter_base = originate_timestamp
    prompt_time_base = parsed.data.receive_timestamp
    round_trip_delay = (destination_timestamp - originate_timestamp) - (
        parsed.data.transmit_timestamp - parsed.data.receive_timestamp
    )
    logger.debug(
        "SyncResponse received; timestamps: "
        "originate={originate}, receive={receive}, "
        "transmit={transmit}, destination={destination}; "
        "computed: prompt_time_perf_counter_base={prompt_time_perf_counter_base}, "
        "prompt_time_base={prompt_time_base}, round_trip_delay={round_trip_delay}",
        originate=repr(originate_timestamp),
        receive=repr(parsed.data.receive_timestamp),
        transmit=repr(parsed.data.transmit_timestamp),
        destination=repr(destination_timestamp),
        prompt_time_perf_counter_base=repr(prompt_time_perf_counter_base),
        prompt_time_base=repr(prompt_time_base),
        round_trip_delay=repr(round_trip_delay),
    )

    uid = gen_uid()
    try:
        logger.debug("uid={uid} sending AuthResponseSuccess")
        await asyncio.wait_for(
            websocket.send_text(
                AuthResponseSuccessPacket(
                    success=True,
                    type="auth_response",
                    uid=uid,
                    data=AuthResponseSuccessPacketData(),
                ).model_dump_json()
            ),
            timeout=SEND_TIMEOUT,
        )
    except asyncio.TimeoutError:
        logger.debug("sending AuthResponseSuccess timed out")
        await close(websocket, logger=logger)
        return

    logger.debug("initial handshake complete")
    return InteractivePromptWatchData(
        core=InteractivePromptWatchCoreData(
            rate=1.0,
            prompt_time_perf_counter_base=prompt_time_perf_counter_base,
            prompt_time_base=prompt_time_base,
            prompt_duration=meta.duration_seconds,
            round_trip_delay=round_trip_delay,
            interactive_prompt_uid=auth_result.result.interactive_prompt_uid,
            bandwidth=bandwidth,
            lookback=lookback,
            lookahead=lookahead,
        ),
        latency_detection=InteractivePromptWatchLatencyDetectionData(
            next_at=time.perf_counter(),
        ),
    )


class InteractivePromptEventPubSubMessage(BaseModel):
    """Describes a message that is published to the pubsub topic for an interactive prompt"""

    uid: str = Field(description="the uid of the new event")
    user_sub: str = Field(description="the sub of the user who created the event")
    session_uid: str = Field(
        description="the uid of the session the event was created in"
    )
    evtype: str = Field(description="the type of the event")
    data: Dict[str, Any] = Field(description="the data of the event")
    icon: Optional[str] = Field(
        description="if there is an icon associated with this event, the uid of the corresponding image file"
    )
    prompt_time: float = Field(description="the prompt time of the event")
    created_at: float = Field(
        description="the unix timestamp of when the event was created"
    )


async def handle_stream(
    websocket: WebSocket,
    *,
    itgs: Itgs,
    logger: LoguruLogger,
    data: InteractivePromptWatchData,
) -> None:
    """Handles streaming events and latency detection packets to the client."""

    latency_future: Optional[asyncio.Task] = None
    recieve_future: asyncio.Task = asyncio.create_task(websocket.receive())

    unsent_events: List[EventBatchPacketDataItem] = []
    """Events that haven't been sent to the client yet, but have been received from the
    pubsub topic."""

    next_event_batch_at: Optional[float] = None
    """the time we intend to send the unsent events to the client"""

    tokens: int = data.core.bandwidth
    last_token_at: float = time.perf_counter()

    redis = await itgs.redis()
    pubsub = redis.pubsub()
    try:
        await pubsub.subscribe(
            f"ps:interactive_prompts:{data.core.interactive_prompt_uid}:events"
        )
        event_future: asyncio.Task = asyncio.create_task(
            pubsub.get_message(ignore_subscribe_messages=True, timeout=5)
        )

        while True:
            if recieve_future.done():
                message = recieve_future.result()
                if message["type"] == "websocket.disconnect":
                    logger.debug("client cleanly disconnected")
                    return

                logger.debug(
                    "client sent unexpected message: type={type}; closing connection",
                    message["type"],
                )
                if latency_future is not None:
                    latency_future.cancel()
                event_future.cancel()
                await close(websocket, logger=logger)
                return

            if latency_future is not None and latency_future.done():
                try:
                    latency_future.result()
                except asyncio.TimeoutError:
                    logger.debug("send latency detection packet timed out")
                    recieve_future.cancel()
                    event_future.cancel()
                    await close(websocket, logger=logger)
                    return

                latency_future = None

            if (
                latency_future is None
                and time.perf_counter() >= data.latency_detection.next_at
            ):
                latency_future = asyncio.create_task(
                    send_latency_detection_packet(websocket, logger=logger, data=data)
                )
                data.latency_detection.next_at = (
                    time.perf_counter() + LATENCY_DETECTION_INTERVAL
                )

            if event_future.done():
                raw_message = event_future.result()
                if raw_message is not None:
                    message = InteractivePromptEventPubSubMessage.parse_raw(
                        raw_message["data"], content_type="application/json"
                    )

                    cur_prompt_time = data.core.prompt_time
                    if (
                        cur_prompt_time - data.core.lookback
                        <= message.prompt_time
                        <= cur_prompt_time + data.core.lookahead
                    ):
                        now = time.perf_counter()
                        delta_sim_time = (now - last_token_at) * data.core.rate
                        bonus_tokens = int(data.core.bandwidth * delta_sim_time)
                        tokens = min(data.core.bandwidth, tokens + bonus_tokens)
                        used_sim_time = bonus_tokens / data.core.bandwidth
                        used_real_time = used_sim_time / data.core.rate
                        last_token_at += used_real_time

                        if tokens > 0:
                            tokens -= 1
                            logger.debug(
                                "received event we intend to send, uid={uid}",
                                uid=message.uid,
                            )
                            unsent_events.append(
                                EventBatchPacketDataItem(
                                    uid=message.uid,
                                    user_sub=message.user_sub,
                                    session_uid=message.session_uid,
                                    evtype=message.evtype,
                                    prompt_time=message.prompt_time,
                                    icon=(
                                        ImageRef(
                                            uid=message.icon,
                                            jwt=await create_image_file_jwt(
                                                itgs, message.icon
                                            ),
                                        )
                                        if message.icon is not None
                                        else None
                                    ),
                                    data=message.data,
                                )
                            )

                            if len(unsent_events) == 1:
                                next_event_batch_at = (
                                    time.perf_counter() + EVENT_BATCH_INTERVAL
                                )
                                logger.debug(
                                    "first event in batch, scheduling batch send for {next_event_batch_at}",
                                    next_event_batch_at=next_event_batch_at,
                                )

                event_future = asyncio.create_task(
                    pubsub.get_message(ignore_subscribe_messages=True, timeout=5)
                )

            now = time.perf_counter()
            if next_event_batch_at is not None and now >= next_event_batch_at:
                uid = gen_uid()
                logger.debug(
                    "uid={uid}: sending {n} events to client at now={now}; undesired_delay={undesired_delay}",
                    uid=repr(uid),
                    n=len(unsent_events),
                    now=now,
                    undesired_delay=now - next_event_batch_at,
                )
                try:
                    await asyncio.wait_for(
                        websocket.send_text(
                            EventBatchPacket(
                                success=True,
                                type="event_batch",
                                uid=gen_uid(),
                                data=EventBatchPacketData(
                                    events=unsent_events,
                                ),
                            ).model_dump_json()
                        ),
                        timeout=SEND_TIMEOUT,
                    )
                    unsent_events = []
                    next_event_batch_at = None
                except asyncio.TimeoutError:
                    logger.debug("sending EventBatchPacket timed out")
                    recieve_future.cancel()
                    event_future.cancel()
                    if latency_future is not None:
                        latency_future.cancel()
                    await close(websocket, logger=logger)
                    return

            if (
                data.core.prompt_time
                > data.core.prompt_duration + data.core.lookback + STAY_PAST_CLOSE_TIME
            ):
                logger.debug("interactive prompt is over; closing connection")
                recieve_future.cancel()
                event_future.cancel()
                if latency_future is not None:
                    latency_future.cancel()
                await close(websocket, logger=logger)
                return

            want_send_next_latency_future: Optional[asyncio.Task] = None
            if latency_future is None:
                now = time.perf_counter()
                if now >= data.latency_detection.next_at:
                    continue

                want_send_next_latency_future = asyncio.create_task(
                    asyncio.sleep(data.latency_detection.next_at - now)
                )

            want_send_event_batch_future: Optional[asyncio.Task] = None
            if next_event_batch_at is not None:
                now = time.perf_counter()
                if now >= next_event_batch_at:
                    continue

                want_send_event_batch_future = asyncio.create_task(
                    asyncio.sleep(next_event_batch_at - now)
                )

            await asyncio.wait(
                [
                    recieve_future,
                    event_future,
                    *(
                        [want_send_next_latency_future]
                        if latency_future is None
                        else [latency_future]
                    ),
                    *(
                        [want_send_event_batch_future]
                        if next_event_batch_at is not None
                        else []
                    ),
                ],
                return_when=asyncio.FIRST_COMPLETED,
            )
            if want_send_next_latency_future is not None:
                want_send_next_latency_future.cancel()
            if want_send_event_batch_future is not None:
                want_send_event_batch_future.cancel()
    finally:
        await pubsub.unsubscribe(
            f"ps:interactive_prompts:{data.core.interactive_prompt_uid}:events"
        )


async def send_latency_detection_packet(
    websocket: WebSocket, *, logger: LoguruLogger, data: InteractivePromptWatchData
) -> None:
    """Sends a latency detection packet to the client

    Raises a asyncio.TimeoutError if the send times out.
    """
    uid = gen_uid()

    # we want as minimum time between calculating the prompt time and
    # sending the packet as possible, so we skip the jsonification step
    # and instead use basic string concatenation
    prefix = f'{{"success":true,"type":"latency_detection","uid":"{uid}","data":{{"expected_receive_prompt_time":'
    suffix = "}}"

    logger.debug("uid={uid} sending LatencyDetectionPacket", uid=repr(uid))
    await asyncio.wait_for(
        websocket.send_text(f"{prefix}{data.core.prompt_time}{suffix}"),
        timeout=SEND_TIMEOUT,
    )
    logger.debug("latency detection packet sent successfully")


async def close(websocket: WebSocket, *, logger: LoguruLogger):
    """Attempts to cleanly close the given websocket"""
    try:
        logger.debug("close()")
        if websocket.state == WebSocketState.DISCONNECTED:
            logger.debug("already closed")
            return

        await asyncio.wait_for(websocket.close(), timeout=CLOSE_TIMEOUT)
        logger.debug("closed cleanly")
    except asyncio.TimeoutError:
        logger.debug("close() timed out")


def gen_uid() -> str:
    """Generates a unique packet uid"""
    return f"oseh_packet_{secrets.token_urlsafe(16)}"
