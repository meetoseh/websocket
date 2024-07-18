from fastapi import APIRouter, WebSocket
from typing import Awaitable, List, Optional, Tuple, cast
import loguru
from loguru._logger import Logger as LoguruLogger
import asyncio

from pydantic import ValidationError
from journals.auth import auth_any
from journals.lib.data import JournalChatData
from journals.lib.packets import (
    AUTH_REQUEST_RESPONSE_ERROR_TYPES,
    AuthRequestPacket,
    AuthRequestResponseErrorPacket,
    AuthResponseSuccessPacket,
    AuthResponseSuccessPacketData,
    ErrorPacketData,
    EventBatchPacket,
    EventBatchPacketData,
    EventBatchPacketDataItem,
    EventBatchPacketDataItemDataError,
    EventBatchPacketDataItemDataChat,
    SegmentData,
)
from journals.lib.internal import (
    JournalChatRedisPacket,
    journal_chat_redis_packet_adapter,
)
from lib.journals.client_keys import get_journal_client_key
from lib.journals.master_keys import get_journal_master_key_for_decryption
from lib.ws.close import close as _close
from functools import partial
from itgs import Itgs
from lib.ws.gen_packet_uid import gen_packet_uid
import io
import time
import base64

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
close = partial(_close, timeout=CLOSE_TIMEOUT)

CLIENT_KEY_TIMEOUT: float = 10.0
"""When we are fetching journal client keys, how long we wait before we give up on the
attempt"""

CLIENT_KEY_RETRIES: int = 2
"""When we are fetching journal client keys, how many times we retry before we give up. 0
for no retries."""

MASTER_KEY_TIMEOUT: float = 10.0
"""When we are fetching journal master keys, how long we wait before we give up on the
attempt"""

MASTER_KEY_RETRIES: int = 2
"""When we are fetching journal master keys, how many times we retry before we give up. 0
for no retries."""

REDIS_EVENT_BATCH_SIZE: int = 10
"""How many events to retrieve from redis via lrange at a time when catching up"""

STAY_PAST_CLOSE_TIME: float = 10.0
"""The maximum time in seconds the client can stay connected after the end of the
prompt before we disconnect them.
"""

TOTAL_CONNECTION_TIMEOUT: float = 60 * 60 * 2
"""The maximum time we keep a connection that seems to be making progress alive"""

assert STAY_PAST_CLOSE_TIME > max(
    SEND_TIMEOUT, RECEIVE_TIMEOUT
), "STAY_PAST_CLOSE_TIME must be greater than SEND_TIMEOUT and RECEIVE_TIMEOUT"


@router.websocket("/chat")
async def stream_chat(websocket: WebSocket):
    """
    When the user engages with the Oseh journal they always do so through the standard
    http interface. If an automatic response will be produced, then the return value
    from that http interface is a journal chat JWT, which must be sent to this endpoint
    so that the automatic response can be streamed asynchronously to the client, as it
    may take a while to produce.

    Besides just providing the response (potentially out of order), this will
    also sometimes provide a way to improve the loading experience (e.g.,
    progress bar, spinner messages, etc).

    See docs/routes/journals/chat.md for more details
    """
    logger = cast(
        LoguruLogger,
        loguru.logger.patch(
            lambda record: record.update(
                {"message": f"journals.chat {id(websocket)=} {record['message']}"}
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
) -> Optional[JournalChatData]:
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
        uid = gen_packet_uid()
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
    if auth_result.result is None:
        uid = gen_packet_uid()
        logger.debug(
            "uid={uid} received AuthRequest with invalid JWT: error type: {error_type}, jwt={jwt}",
            uid=repr(uid),
            error_type=repr(auth_result.error_type),
            jwt=repr(parsed.data.jwt),
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

    for retry_counter in range(CLIENT_KEY_RETRIES + 1):
        logger.debug(
            "acquiring journal client key with uid={journal_client_key_uid} (retry: {retry})",
            journal_client_key_uid=auth_result.result.journal_client_key_uid,
            retry=retry_counter,
        )
        try:
            key = await asyncio.wait_for(
                get_journal_client_key(
                    itgs,
                    user_sub=auth_result.result.user_sub,
                    journal_client_key_uid=auth_result.result.journal_client_key_uid,
                    read_consistency="none" if retry_counter == 0 else "weak",
                ),
                timeout=CLIENT_KEY_TIMEOUT,
            )

            if key.type in ("revoked", "parse_error"):
                uid = gen_packet_uid()
                logger.error(
                    "uid={uid} permanent failure retrieving journal client key: error type: {error_type}",
                    uid=repr(uid),
                    error_type=repr(key.type),
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
                                    message="failed to fetch journal client key",
                                ),
                            ).model_dump_json()
                        ),
                        timeout=SEND_TIMEOUT,
                    )
                except asyncio.TimeoutError:
                    logger.debug("sending AuthRequestResponseErrorPacket timed out")

                await close(websocket, logger=logger)
                return

            if key.type == "success":
                break
        except asyncio.TimeoutError:
            logger.debug("acquiring journal client key timed out")
            continue
        except Exception as e:
            logger.exception("acquiring journal client key failed")
            await close(websocket, logger=logger)
            return
    else:
        uid = gen_packet_uid()
        logger.error(
            "uid={uid} exceeded retries retrieving journal client key: error type: {error_type}",
            uid=repr(uid),
            error_type=repr(key.type),
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
                            message="failed to fetch journal client key",
                        ),
                    ).model_dump_json()
                ),
                timeout=SEND_TIMEOUT,
            )
        except asyncio.TimeoutError:
            logger.debug("sending AuthRequestResponseErrorPacket timed out")

        await close(websocket, logger=logger)
        return

    uid = gen_packet_uid()
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
    return JournalChatData(
        journal_entry_uid=auth_result.result.journal_entry_uid,
        journal_chat_uid=auth_result.result.journal_chat_uid,
        journal_client_key_uid=auth_result.result.journal_client_key_uid,
        user_sub=auth_result.result.user_sub,
        journal_client_key=key.journal_client_key,
        journal_master_keys=dict(),
    )


IsFromSync = bool


async def handle_stream(
    websocket: WebSocket,
    *,
    itgs: Itgs,
    logger: LoguruLogger,
    data: JournalChatData,
) -> None:
    websocket_receive_future: asyncio.Task = asyncio.create_task(websocket.receive())

    redis = await itgs.redis()
    pubsub = redis.pubsub()
    await pubsub.subscribe(f"ps:journal_chats:{data.journal_chat_uid}:events")
    try:
        pubsub_event_future: asyncio.Task = asyncio.create_task(
            pubsub.get_message(ignore_subscribe_messages=True, timeout=5)
        )

        sent_up_to_exclusive = 0
        sync_done = False
        redis_lrange_future: Optional[asyncio.Future] = None

        events_queue: List[Tuple[IsFromSync, bytes]] = []
        event_processing_future: Optional[asyncio.Future[bool]] = None

        to_send_events_queue: List[EventBatchPacketDataItem] = []
        send_future: Optional[asyncio.Future] = None

        seen_terminating_event_at: Optional[float] = None
        close_timeout_future: Optional[asyncio.Future] = None

        leaked_timeout_future = asyncio.create_task(
            asyncio.sleep(TOTAL_CONNECTION_TIMEOUT)
        )

        async def process_event(is_from_sync: bool, raw: bytes) -> bool:
            nonlocal sync_done, sent_up_to_exclusive

            reader = io.BytesIO(raw)
            key_uid_size = int.from_bytes(reader.read(4), "big")
            key_uid_raw = reader.read(key_uid_size)
            key_uid = key_uid_raw.decode("utf-8")

            if key_uid not in data.journal_master_keys:
                for retry_counter in range(MASTER_KEY_RETRIES):
                    logger.debug(
                        "acquiring journal master key with uid={journal_master_key_uid} (retry: {retry})",
                        journal_master_key_uid=key_uid,
                        retry=retry_counter,
                    )

                    try:
                        result = await get_journal_master_key_for_decryption(
                            itgs,
                            user_sub=data.user_sub,
                            journal_master_key_uid=key_uid,
                        )
                    except Exception as e:
                        logger.exception("acquiring journal master key failed")
                        return False

                    if result.type in ("revoked", "parse_error"):
                        logger.error(
                            "permanent failure retrieving journal master key: error type: {error_type}",
                            error_type=result.type,
                        )
                        to_send_events_queue.append(
                            EventBatchPacketDataItemDataError(
                                type="error",
                                message="failed to decrypt",
                                detail="failed to terminate internal journal encryption for use in external journal encryption",
                            )
                        )
                        return False

                    if result.type == "success":
                        data.journal_master_keys[key_uid] = result.journal_master_key
                        break
                else:
                    logger.error(
                        "exceeded retries retrieving journal master key: error type: {error_type}",
                        error_type=result.type,
                    )
                    to_send_events_queue.append(
                        EventBatchPacketDataItemDataError(
                            type="error",
                            message="failed to decrypt",
                            detail="a temporary issue caused us to fail to terminate internal journal encryption for use in external journal encryption",
                        )
                    )
                    return False

            key = data.journal_master_keys[key_uid]

            encrypted_size = int.from_bytes(reader.read(8), "big")
            encrypted_blob = reader.read(encrypted_size)
            decrypted_blob = key.decrypt(
                encrypted_blob, ttl=None if is_from_sync else 120
            )
            parsed_blob = journal_chat_redis_packet_adapter.validate_json(
                decrypted_blob
            )

            if parsed_blob.counter < sent_up_to_exclusive:
                return True

            if parsed_blob.counter == sent_up_to_exclusive:
                sent_up_to_exclusive += 1
                if parsed_blob.type == "mutations":
                    raw_segment_data = SegmentData(mutations=parsed_blob.mutations)
                    encrypted_segment_data = data.journal_client_key.encrypt(
                        raw_segment_data.__pydantic_serializer__.to_json(
                            raw_segment_data
                        )
                    )
                    to_send_events_queue.append(
                        EventBatchPacketDataItemDataChat(
                            type="chat",
                            encrypted_segment_data=encrypted_segment_data.decode(
                                "ascii"
                            ),
                            more=parsed_blob.more,
                        )
                    )
                    return True

                assert parsed_blob.type == "passthrough"
                to_send_events_queue.append(parsed_blob.event)
                if parsed_blob.event.type == "error":
                    logger.warning(
                        "detected error passthrough packet at counter {counter}",
                        counter=repr(parsed_blob.counter),
                    )
                    return False
                return True

            logger.warning(
                "ignoring out of order event; from sync? {from_sync}, counter: {counter}, expected: {expected}",
                from_sync=repr(is_from_sync),
                counter=repr(parsed_blob.counter),
                expected=repr(sent_up_to_exclusive),
            )
            sync_done = False
            return True

        while True:
            if websocket_receive_future.done():
                try:
                    raw = websocket_receive_future.result()
                except Exception as e:
                    logger.exception("receive_future failed")
                    await close(websocket, logger=logger)
                    break

                if raw["type"] == "websocket.disconnect":
                    logger.debug("client cleanly disconnected")
                    break

                logger.debug("received unexpected value from client, disconnecting")
                await close(websocket, logger=logger)
                break

            if not sync_done and redis_lrange_future is None:
                redis_lrange_future = asyncio.create_task(
                    cast(
                        Awaitable[list],
                        redis.lrange(
                            f"journal_chats:{data.journal_chat_uid}:events".encode("utf-8"),  # type: ignore
                            sent_up_to_exclusive,
                            sent_up_to_exclusive + REDIS_EVENT_BATCH_SIZE - 1,
                        ),
                    )
                )
                continue

            if redis_lrange_future is not None and redis_lrange_future.done():
                try:
                    res = cast(List[bytes], redis_lrange_future.result())
                except Exception:
                    logger.exception("redis_lrange_future failed")
                    await close(websocket, logger=logger)
                    break

                redis_lrange_future = None
                for item in res:
                    events_queue.append((True, item))

                if len(res) < REDIS_EVENT_BATCH_SIZE:
                    sync_done = True
                continue

            if pubsub_event_future.done():
                try:
                    raw = pubsub_event_future.result()
                except Exception:
                    logger.exception("pubsub_event_future failed")
                    await close(websocket, logger=logger)
                    break

                if raw is not None:
                    events_queue.append((False, raw["data"]))

                pubsub_event_future = asyncio.create_task(
                    pubsub.get_message(ignore_subscribe_messages=True, timeout=5)
                )
                continue

            if events_queue and event_processing_future is None:
                event_processing_future = asyncio.create_task(
                    process_event(*events_queue.pop(0))
                )
                continue

            if event_processing_future is not None and event_processing_future.done():
                try:
                    res = event_processing_future.result()
                except Exception:
                    logger.exception("event_processing_future failed")
                    await close(websocket, logger=logger)
                    break

                event_processing_future = None

                if not res:
                    logger.debug("event_processing_future requested shutdown")
                    if seen_terminating_event_at is None:
                        seen_terminating_event_at = time.time()

                continue

            if to_send_events_queue and send_future is None:
                sending = to_send_events_queue[:10]
                to_send_events_queue = to_send_events_queue[10:]

                uid = gen_packet_uid()
                logger.debug(
                    "{uid} sending batch of {num} events", uid=uid, num=len(sending)
                )
                send_future = asyncio.create_task(
                    asyncio.wait_for(
                        websocket.send_text(
                            EventBatchPacket(
                                success=True,
                                type="event_batch",
                                uid=uid,
                                data=EventBatchPacketData(events=sending),
                            ).model_dump_json()
                        ),
                        timeout=SEND_TIMEOUT,
                    )
                )
                continue

            if send_future is not None and send_future.done():
                try:
                    send_future.result()
                except Exception:
                    logger.exception("send_future failed")
                    await close(websocket, logger=logger)
                    break

                send_future = None
                continue

            if (
                not to_send_events_queue
                and send_future is None
                and seen_terminating_event_at is not None
                and close_timeout_future is None
            ):
                close_timeout_future = asyncio.create_task(
                    asyncio.sleep(STAY_PAST_CLOSE_TIME)
                )
                continue

            if close_timeout_future is not None and close_timeout_future.done():
                logger.debug("closing websocket after timeout")
                await close(websocket, logger=logger)
                break

            if leaked_timeout_future.done():
                logger.debug("closing websocket after leak timeout")
                await close(websocket, logger=logger)
                break

            await asyncio.wait(
                [
                    f
                    for f in (
                        websocket_receive_future,
                        redis_lrange_future,
                        pubsub_event_future,
                        event_processing_future,
                        send_future,
                        close_timeout_future,
                        leaked_timeout_future,
                    )
                    if f is not None
                ],
                return_when=asyncio.FIRST_COMPLETED,
            )

        websocket_receive_future.cancel()
        pubsub_event_future.cancel()
        if redis_lrange_future is not None:
            redis_lrange_future.cancel()
        if event_processing_future is not None:
            event_processing_future.cancel()
        if send_future is not None:
            send_future.cancel()
        if close_timeout_future is not None:
            close_timeout_future.cancel()
        leaked_timeout_future.cancel()

    finally:
        await pubsub.unsubscribe()
        await pubsub.aclose()
