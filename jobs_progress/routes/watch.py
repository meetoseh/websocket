import secrets
import time
from typing import Iterable, List, Optional, cast
from fastapi import APIRouter, WebSocket
from fastapi.websockets import WebSocketState
import loguru
from loguru._logger import Logger as LoguruLogger
import asyncio

from pydantic import TypeAdapter, ValidationError
import traceback
from error_middleware import handle_error
from itgs import Itgs
from jobs_progress.auth import auth_any
from jobs_progress.lib.convert_to_outgoing import convert_to_outgoing
from jobs_progress.lib.data import (
    JobsProgressInitialEventsData,
    JobsProgressWatchCoreData,
    JobsProgressWatchData,
    JobsProgressWatchTimeoutData,
)
from jobs_progress.lib.packets import (
    AuthRequestPacket,
    AuthResponseErrorPacket,
    ErrorPacketData,
    AUTH_RESPONSE_ERROR_TYPES,
    AuthResponseSuccessPacket,
    AuthResponseSuccessPacketData,
    EventBatchPacket,
    EventBatchPacketData,
    GenericServerErrorTypes,
    JobProgressIncomingModel,
    JobProgressOutgoingModel,
    ServerGenericErrorPacket,
)
from jobs_progress.lib.subscribe_events import EventSubscription


router = APIRouter()
event_adapter = TypeAdapter(JobProgressIncomingModel)


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

EVENT_BATCH_INTERVAL: float = 0.25
"""After receiving an event, we wait this duration before sending it and any other
events we received during that interval. This can be a significant throughput boost
if there are a lot of events, but it also sets a minimum latency for events to be
received by the client.
"""

IDLE_TIMEOUT: float = 1800.0
"""The maximum time in seconds the client can stay connected without receiving
any events before we close the connection from our end.
"""

STAY_PAST_CLOSE_TIME: float = 10.0
"""The maximum time in seconds the client can stay connected after a `final`
event before we close the connection from our end.
"""

assert STAY_PAST_CLOSE_TIME > max(
    SEND_TIMEOUT, RECEIVE_TIMEOUT
), "STAY_PAST_CLOSE_TIME must be greater than SEND_TIMEOUT and RECEIVE_TIMEOUT"

REDIS_EVENT_TIMEOUT: int = 5
"""How many seconds we wait for a new event from redis before repeating the
asyncio future. This is mostly just an implementation detail: changing this
value within reasonable bounds should have no impact on behavior assuming
our redis library handles cancellation correctly. note that the redis library
doesn't quite do cancellation correctly so this will have some impact on how
quickly connections are closed
"""


@router.websocket("/live")
async def watch_job_progress(websocket: WebSocket):
    """See docs/routes/jobs/live.md"""
    logger = cast(
        LoguruLogger,
        loguru.logger.patch(
            lambda record: record.update(
                {"message": f"jobs.live {id(websocket)=} {record['message']}"}
            )
        ),
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
) -> Optional[JobsProgressWatchData]:
    """Processes the initial handshake and prepares the context data for streaming."""
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
        parsed = AuthRequestPacket.model_validate_json(
            raw["bytes"] if raw.get("bytes") else raw["text"]
        )
    except ValidationError as e:
        uid = gen_uid()
        logger.exception("uid={uid} received unparseable AuthRequest", uid=repr(uid))
        try:
            await asyncio.wait_for(
                websocket.send_text(
                    AuthResponseErrorPacket(
                        success=False,
                        type="error",
                        uid=uid,
                        data=ErrorPacketData[AUTH_RESPONSE_ERROR_TYPES](
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
        auth_result.result is None
        or auth_result.result.job_progress_uid != parsed.data.job_uid
    ):
        uid = gen_uid()
        logger.debug(
            "uid={uid} received AuthRequest with invalid JWT: jwt={jwt} (success={success}, requested job_uid={job_uid})",
            uid=repr(uid),
            jwt=repr(parsed.data.jwt),
            success=repr(auth_result.result is not None),
            job_uid=repr(parsed.data.job_uid),
        )
        try:
            await asyncio.wait_for(
                websocket.send_text(
                    AuthResponseErrorPacket(
                        success=False,
                        type="error",
                        uid=uid,
                        data=ErrorPacketData[AUTH_RESPONSE_ERROR_TYPES](
                            code=403, type="forbidden", message="invalid JWT"
                        ),
                    ).model_dump_json()
                ),
                timeout=SEND_TIMEOUT,
            )
        except asyncio.TimeoutError:
            logger.debug("sending AuthResponseErrorPacket timed out")

        await close(websocket, logger=logger)
        return

    logger.debug(
        "valid AuthRequest for job_uid={job_uid}, checking for initial events",
        job_uid=repr(parsed.data.job_uid),
    )

    redis = await itgs.redis()
    raw_events = await redis.lrange(
        f"jobs:progress:events:{parsed.data.job_uid}".encode("utf-8"),  # type: ignore
        0,
        -1,
    )
    if not raw_events:
        logger.warning("no events have been posted to that job, responding with 404")
        try:
            logger.debug("sending AuthResponseErrorPacket")
            await asyncio.wait_for(
                websocket.send_text(
                    AuthResponseErrorPacket(
                        success=False,
                        type="error",
                        uid=gen_uid(),
                        data=ErrorPacketData[AUTH_RESPONSE_ERROR_TYPES](
                            code=404,
                            type="not_found",
                            message="job progress not initialized",
                        ),
                    ).model_dump_json()
                ),
                timeout=SEND_TIMEOUT,
            )
        except asyncio.TimeoutError:
            logger.debug("sending AuthResponseErrorPacket timed out")

        await close(websocket, logger=logger)
        return

    try:
        initial_events = [
            await convert_to_outgoing(itgs, event_adapter.validate_json(event))
            for event in raw_events
        ]
    except ValidationError as e:
        logger.warning(
            "invalid event data in redis, responding with 500",
            exc_info=e,
        )
        try:
            logger.debug("sending ErrorPacket")
            await asyncio.wait_for(
                websocket.send_text(
                    ServerGenericErrorPacket(
                        success=False,
                        type="server_error",
                        uid=gen_uid(),
                        data=ErrorPacketData[GenericServerErrorTypes](
                            code=500,
                            type="internal_server_error",
                            message="invalid event data in redis",
                        ),
                    ).model_dump_json()
                ),
                timeout=SEND_TIMEOUT,
            )
        except asyncio.TimeoutError:
            logger.debug("sending AuthResponseErrorPacket timed out")

        await close(websocket, logger=logger)
        return

    logger.debug(
        "loaded {num_events} initial events", num_events=repr(len(initial_events))
    )

    saw_final_event = any(event.type == "final" for event in initial_events)
    if saw_final_event:
        logger.debug(
            "saw final event in initial events, connection will be auto-closed after "
            "STAY_PAST_CLOSE_TIME={STAY_PAST_CLOSE_TIME} seconds",
            STAY_PAST_CLOSE_TIME=repr(STAY_PAST_CLOSE_TIME),
        )

    try:
        logger.debug("sending AuthResponseSuccessPacket")
        await asyncio.wait_for(
            websocket.send_text(
                AuthResponseSuccessPacket(
                    success=True,
                    type="auth_response",
                    uid=gen_uid(),
                    data=AuthResponseSuccessPacketData(),
                ).model_dump_json()
            ),
            timeout=SEND_TIMEOUT,
        )
    except asyncio.TimeoutError:
        logger.debug("sending AuthResponseSuccessPacket timed out")
        await close(websocket, logger=logger)
        return

    handshake_finished_at = time.time()
    logger.debug(
        "initial handshake complete at {handshake_finished_at}",
        handshake_finished_at=repr(handshake_finished_at),
    )
    return JobsProgressWatchData(
        core=JobsProgressWatchCoreData(job_progress_uid=parsed.data.job_uid),
        timeout=JobsProgressWatchTimeoutData(
            last_event_at=handshake_finished_at,
            final_event_at=handshake_finished_at if saw_final_event else None,
        ),
        initial_events=JobsProgressInitialEventsData(events=initial_events),
    )


async def handle_stream(
    websocket: WebSocket,
    *,
    itgs: Itgs,
    logger: LoguruLogger,
    data: JobsProgressWatchData,
):
    """Core loop for streaming events to the client."""

    websocket_recieve_future = asyncio.create_task(websocket.receive())
    websocket_send_future: Optional[asyncio.Task[None]] = None

    unsent_events: List[JobProgressOutgoingModel] = (
        [] if data.initial_events.events is None else data.initial_events.events
    )
    """events that are queued to be sent in the next batch"""
    data.initial_events.events = None

    next_event_batch_at: Optional[float] = None if not unsent_events else time.time()
    """the time we intend to send the unsent events to the client"""

    async with EventSubscription(
        job_progress_uid=data.core.job_progress_uid, conn=await itgs.redis()
    ) as event_subscription:
        redis_events_future = asyncio.create_task(
            event_subscription.next(timeout=REDIS_EVENT_TIMEOUT)
        )

        def cancel_pending() -> None:
            websocket_recieve_future.cancel()
            redis_events_future.cancel()

            if websocket_send_future is not None:
                websocket_send_future.cancel()

        while True:
            # Each loop will check on all the futures, then at the end create
            # the appropriate select(2)-style call to ensure the next iteration
            # happens as soon as something happens
            loop_at = time.time()

            if websocket_recieve_future.done():
                if websocket_recieve_future.exception() is not None:
                    logger.debug(
                        "websocket_recieve_future raised an exception, closing connection"
                    )
                    cancel_pending()
                    await close(websocket, logger=logger)
                    return

                message = websocket_recieve_future.result()
                if message["type"] == "websocket.disconnect":
                    logger.debug("client cleanly disconnected")
                    cancel_pending()
                    return

                logger.debug(
                    "client sent unexpected message: type={type}; closing connection",
                    type=repr(message["type"]),
                )
                cancel_pending()
                await close(websocket, logger=logger)
                return

            if websocket_send_future is not None and websocket_send_future.done():
                send_exc = websocket_send_future.exception()
                if send_exc is not None:
                    if isinstance(send_exc, asyncio.TimeoutError):
                        logger.debug(
                            "websocket_send_future timed out, closing connection"
                        )
                    else:
                        logger.debug(
                            "websocket_send_future raised an exception, closing connection\n\n{exception}",
                            exception=traceback.format_exception(send_exc),
                        )
                    cancel_pending()
                    await close(websocket, logger=logger)
                    return

                websocket_send_future = None

            if redis_events_future.done():
                events_exc = redis_events_future.exception()
                if events_exc is not None:
                    logger.debug(
                        "redis_events_future raised an exception, closing connection\n\n{exception}",
                        exception=traceback.format_exception(events_exc),
                    )
                    cancel_pending()
                    await close(websocket, logger=logger)
                    await handle_error(
                        events_exc,
                        extra_info=f"while processing `job progress uid={data.core.job_progress_uid}`",
                    )
                    return

                new_events = redis_events_future.result()
                if new_events:
                    logger.debug(
                        "received {num_events} new events from redis",
                        num_events=repr(len(new_events)),
                    )
                    for e in new_events:
                        unsent_events.append(await convert_to_outgoing(itgs, e))
                    data.timeout.last_event_at = loop_at
                    if next_event_batch_at is None:
                        next_event_batch_at = loop_at + EVENT_BATCH_INTERVAL
                        logger.debug(
                            "queued new event batch in {EVENT_BATCH_INTERVAL}s",
                            EVENT_BATCH_INTERVAL=repr(EVENT_BATCH_INTERVAL),
                        )

                    if data.timeout.final_event_at is not None:
                        logger.debug(
                            "received more events even though we saw a final event, "
                            "extending final event timeout"
                        )
                        data.timeout.final_event_at = loop_at
                    elif any(event.type == "final" for event in new_events):
                        logger.debug(
                            "received final event, connection will be auto-closed after "
                            "STAY_PAST_CLOSE_TIME={STAY_PAST_CLOSE_TIME} seconds",
                            STAY_PAST_CLOSE_TIME=repr(STAY_PAST_CLOSE_TIME),
                        )
                        data.timeout.final_event_at = loop_at

                redis_events_future = asyncio.create_task(
                    event_subscription.next(timeout=REDIS_EVENT_TIMEOUT)
                )

            if (
                websocket_send_future is None
                and unsent_events
                and next_event_batch_at is not None
                and loop_at >= next_event_batch_at
            ):
                logger.debug(
                    "sending batch of {num_events} events",
                    num_events=repr(len(unsent_events)),
                )
                events_in_batch = unsent_events
                unsent_events = []
                next_event_batch_at = None

                websocket_send_future = asyncio.create_task(
                    asyncio.wait_for(
                        websocket.send_text(
                            EventBatchPacket(
                                success=True,
                                type="event_batch",
                                uid=gen_uid(),
                                data=EventBatchPacketData(events=events_in_batch),
                            ).model_dump_json()
                        ),
                        timeout=SEND_TIMEOUT,
                    )
                )

            time_until_idle_timeout = (
                data.timeout.last_event_at + IDLE_TIMEOUT - loop_at
            )
            if time_until_idle_timeout <= 0:
                logger.debug(
                    "idle timeout of {idle_timeout}s reached, closing connection",
                    idle_timeout=repr(IDLE_TIMEOUT),
                )
                cancel_pending()
                await close(websocket, logger=logger)
                return

            time_until_stay_after_close_timeout = (
                data.timeout.final_event_at + STAY_PAST_CLOSE_TIME - loop_at
                if data.timeout.final_event_at is not None
                else None
            )
            if (
                time_until_stay_after_close_timeout is not None
                and time_until_stay_after_close_timeout <= 0
            ):
                logger.debug(
                    "stay after close timeout of {stay_after_close_timeout}s reached, closing connection",
                    stay_after_close_timeout=repr(STAY_PAST_CLOSE_TIME),
                )
                cancel_pending()
                await close(websocket, logger=logger)
                return

            time_until_batch_timeout = (
                next_event_batch_at - loop_at
                if next_event_batch_at is not None
                else None
            )
            if time_until_batch_timeout is not None and time_until_batch_timeout <= 0:
                # waiting on a send
                time_until_batch_timeout = None

            time_until_next_timeout = min(
                cast(
                    Iterable[float],
                    [
                        v
                        for v in [
                            time_until_idle_timeout,
                            time_until_stay_after_close_timeout,
                            time_until_batch_timeout,
                        ]
                        if v is not None
                    ],
                )
            )
            assert (
                time_until_next_timeout > 0
            ), "time_until_next_timeout must be positive"

            select_from = cast(
                List[Optional[asyncio.Task]],
                [
                    websocket_recieve_future,
                    redis_events_future,
                    websocket_send_future,
                ],
            )
            await asyncio.wait(
                [v for v in select_from if v is not None],
                timeout=time_until_next_timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )


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
