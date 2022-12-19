"""Often whenever using the local cache it's necessary to make a perpetual
subscription to a channel to detect when the local cache needs to be cleared.
This is a fairly simple task when you create a new process for the job, however,
opening one perpetual connection to redis for each cache key quickly gets out of
control.

This module facilitates the same behavior, but allows everyone who needs a
perpetual subscription to share the same connection.

This is not suitable for connections which have a lot of traffic - they should
continue to have a dedicated connection. This is not suitable for short-lived
subscriptions, which should also use a dedicated connection.
"""
import time
from typing import (
    AsyncIterator,
    Dict,
    List,
    NoReturn as Never,
    Optional,
    Tuple,
)
from error_middleware import handle_error
from itgs import Itgs
import multiprocessing as mp
import multiprocessing.connection
import threading
import traceback
import queue
import asyncio
import secrets
import sys
import os
import loguru


class PPSShutdownException(Exception):
    """Raised when the perpetual sub process is shutting down / has shut down
    or the subscription has been removed.
    """

    def __init__(self, message: str):
        super().__init__(message)


class PerpetualPubSub:
    """Acts as an interface to a perpetual pub sub connection. When initialized,
    this will start a new process which will handle the perpetual connection.
    In order to avoid an explosion of processes, care should be taken to ensure
    this is only initialized within the main process.
    """

    def __init__(self):
        self.subscribe_queue = mp.Queue()
        """A queue where the elements in the queue are tuples of the form
        (uid: str, channel: str or bytes, send_pipe: multiprocessing.connection.Connection).
        The uid is a unique identifier for the subscription, and
        the channel is the channel to subscribe to.

        Once the subscription is confirmed, b'ready' will be sent (via send_bytes). From
        there, messages will be sent to the pipe via send_bytes as they arrive.

        If the pipe breaks, the subscription will be removed. If the subscription is
        removed via the unsubscribe_queue, b'closed' will be sent (via send_bytes)
        when no more messages will be sent, and it will be up to the client to close
        the pipe.

        This is pushed to from the main fastapi process and read from by the perpetual
        pub sub process.
        """

        self.unsubscribe_queue = mp.Queue()
        """A queue where the elements in the queue are tuples of the form
        (uid: str, channel: str or bytes). The uid is a unique identifier for the subscription, and
        the channel that the subscription is on.

        This is pushed to from the main fastapi process and read from by the perpetual
        pub sub process.
        """

        mp.Process(
           target=self._run_in_background, daemon=True
        ).start()

    def _run_in_background(self) -> Never:
        """This is the function which is run in the background process. It will create
        an asyncio event loop and call _run_in_background_async.
        """
        asyncio.run(self._run_in_background_async())

    async def _run_in_background_async(self) -> Never:
        """Runs continuously until a termination signal is received. This maintains
        the connection to redis and listens for new subscriptions and unsubscriptions
        from other processes.
        """
        logger = loguru.logger

        logger.info(
            "Starting perpetual pub sub process on pid {pid}, tid {tid}",
            pid=os.getpid(),
            tid=threading.get_ident(),
        )
        failures_times: List[float] = []

        subscriptions_by_channel: Dict[
            str, Dict[str, multiprocessing.connection.Connection]
        ] = dict()
        # A mapping from channel to a mapping from uid to send_pipe.

        failed_unsubscribes: List[Tuple[str, float]] = []
        # If we receive an unsubscribe message we don't have a subscribe message for, it's
        # possible we just haven't gotten to it yet. We keep track of the uids and when the
        # unsubscribe message was received. If we don't get a subscribe message for it within
        # 60 seconds, we warn slack and forget about it

        recently_broken_subscribes: List[Tuple[str, float]] = []
        # If we receive an unsubscribe message we don't have a subscribe message for, it's
        # possible we detected the pipe was broken and removed the subscription. We keep track
        # of the uids we've recently removed and when we removed them. If we get a unsubscribe
        # message for one of these within 60 seconds, we can provide a more useful error message.
        #
        # This variable can be interpreted as "subcriptions which we recently removed because
        # we detected the pipe was broken"
        try:
            while True:
                try:
                    async with Itgs() as itgs:
                        logger.info("Connecting to redis")
                        redis = await itgs.redis()
                        pubsub = redis.pubsub()

                        if subscriptions_by_channel:
                            logger.debug(
                                "Resubscribing to {channels}",
                                channels=", ".join(subscriptions_by_channel),
                            )
                            await pubsub.subscribe(*subscriptions_by_channel)

                        message_task = (
                            asyncio.create_task(
                                pubsub.get_message(
                                    ignore_subscribe_messages=True, timeout=1
                                )
                            )
                            if subscriptions_by_channel
                            else None
                        )
                        while True:
                            while True:
                                try:
                                    (
                                        uid,
                                        channel,
                                        send_pipe,
                                    ) = self.subscribe_queue.get_nowait()
                                except queue.Empty:
                                    break

                                logger.debug(
                                    "PerpetualPubSub received subscribe request for {uid} on {channel}",
                                    uid=uid,
                                    channel=channel,
                                )
                                idx_in_failed_unsubscribes = next(
                                    (
                                        i
                                        for i, (failed_uid, _) in enumerate(
                                            failed_unsubscribes
                                        )
                                        if failed_uid == uid
                                    ),
                                    None,
                                )
                                if idx_in_failed_unsubscribes is not None:
                                    logger.info(
                                        "Found matching subscribe message for failed unsubscribe: {uid}",
                                        uid=uid,
                                    )
                                    failed_unsubscribes.pop(idx_in_failed_unsubscribes)
                                    continue

                                try:
                                    send_pipe.send_bytes(b"ready")
                                except BrokenPipeError:
                                    logger.info(
                                        "Pipe broken before subscription confirmed: {uid}",
                                        uid=uid,
                                    )
                                    continue

                                logger.debug(
                                    "Successfully sent ready message to {uid}", uid=uid
                                )

                                # in case subscribing fails, make sure we're internally up-to-date first
                                need_subscribe = False
                                if channel not in subscriptions_by_channel:
                                    subscriptions_by_channel[channel] = dict()
                                    need_subscribe = True

                                subscriptions_by_channel[channel][uid] = send_pipe

                                if need_subscribe:
                                    logger.debug(
                                        "PerpetualPubSub subscribing to {channel}",
                                        channel=channel,
                                    )
                                    await pubsub.subscribe(channel)

                            while True:
                                try:
                                    uid, channel = self.unsubscribe_queue.get_nowait()
                                except queue.Empty:
                                    break

                                logger.debug(
                                    "PerpetualPubSub received unsubscribe request for {uid} on {channel}",
                                    uid=uid,
                                    channel=channel,
                                )

                                if (
                                    channel not in subscriptions_by_channel
                                    or uid not in subscriptions_by_channel[channel]
                                ) and any(
                                    uid == failed_uid
                                    for failed_uid, _ in recently_broken_subscribes
                                ):
                                    logger.warning(
                                        "PerpetualPubSub received unsubscribe request for {uid} on {channel}, but that pipe "
                                        "was already broken so there was no need for the message. Ignoring.",
                                    )
                                    continue

                                if channel not in subscriptions_by_channel:
                                    logger.info(
                                        "PerpetualPubSub received unsubscribe request for {uid} on {channel}, but we don't have any subscriptions for that channel",
                                        uid=uid,
                                        channel=channel,
                                    )
                                    failed_unsubscribes.append((uid, time.time()))
                                    continue

                                if uid not in subscriptions_by_channel[channel]:
                                    logger.info(
                                        "PerpetualPubSub received unsubscribe request for {uid} on {channel}, but we don't have a subscription for that uid",
                                        uid=uid,
                                        channel=channel,
                                    )
                                    failed_unsubscribes.append((uid, time.time()))
                                    continue

                                old_send_pipe = subscriptions_by_channel[channel][uid]
                                del subscriptions_by_channel[channel][uid]

                                if not subscriptions_by_channel[channel]:
                                    del subscriptions_by_channel[channel]

                                    logger.debug(
                                        "PerpetualPubSub unsubscribing from {channel}",
                                        channel=channel,
                                    )
                                    await pubsub.unsubscribe(channel)

                                try:
                                    old_send_pipe.send_bytes(b"closed")
                                except BrokenPipeError:
                                    logger.debug(
                                        "PerpetualPubSub detected broken pipe for {uid} on {channel} when sending closed message",
                                        uid=uid,
                                        channel=channel,
                                    )

                            now = time.time()
                            permanently_failed_unsubscribes = [
                                uid for uid, t in failed_unsubscribes if t < now - 60
                            ]
                            if permanently_failed_unsubscribes:
                                failed_unsubscribes = failed_unsubscribes[
                                    len(permanently_failed_unsubscribes) :
                                ]

                                slack = await itgs.slack()
                                await slack.send_web_error_message(
                                    "PerpetualPubSub received unsubscribe requests for uids that we don't have subscriptions for. Uids: {}".format(
                                        permanently_failed_unsubscribes
                                    )
                                )

                            recently_broken_subscribes = [
                                (uid, t)
                                for uid, t in recently_broken_subscribes
                                if t > now - 60
                            ]

                            if message_task is None:
                                if not subscriptions_by_channel:
                                    await asyncio.sleep(1)
                                    continue
                                message_task = asyncio.create_task(pubsub.get_message())

                            message = await message_task
                            message_task = None
                            if message is None:
                                continue

                            if message["type"] != "message":
                                continue

                            channel = message["channel"].decode("utf-8")
                            if channel not in subscriptions_by_channel:
                                continue

                            raw_message = message["data"]
                            broken_uids: List[str] = []
                            for uid, send_pipe in subscriptions_by_channel[
                                channel
                            ].items():
                                try:
                                    send_pipe.send_bytes(raw_message)
                                except BrokenPipeError:
                                    broken_uids.append(uid)

                            if broken_uids:
                                logger.info(
                                    "PerpetualPubSub found broken pipes for uids {uids} on channel {channel}. Unsubscribing.",
                                    uids=broken_uids,
                                    channel=channel,
                                )
                                for uid in broken_uids:
                                    recently_broken_subscribes.append(
                                        (uid, time.time())
                                    )
                                    del subscriptions_by_channel[channel][uid]

                                if not subscriptions_by_channel[channel]:
                                    del subscriptions_by_channel[channel]

                                    logger.debug(
                                        "PerpetualPubSub unsubscribing from {channel}",
                                        channel=channel,
                                    )
                                    await pubsub.unsubscribe(channel)
                except Exception as e:
                    asyncio.ensure_future(handle_error(e))

                    now = time.time()
                    failures_times = [t for t in failures_times if t > now - 60]
                    failures_times.append(now)

                    if len(failures_times) >= 5:
                        slack = await itgs.slack()
                        await slack.send_ops_message(
                            "web-backend PerpetualPubSub _run_in_background_async is failing too often. Exiting."
                        )
                        sys.exit(1)

                    await asyncio.sleep(len(failures_times))
        except BaseException:
            # we've been interrupted, notify all the subscribers that we're shutting down
            for channel, subscriptions in subscriptions_by_channel.items():
                for uid, send_pipe in subscriptions.items():
                    try:
                        send_pipe.send_bytes(b"closed")
                    except BrokenPipeError:
                        pass
        finally:
            logger.info("PerpetualPubSub shutting down")


class PPSSubscription:
    """A convenient interface to a subscription to a perpetual pub sub connection. This
    acts as an async context manager, and will automatically unsubscribe from the
    channel when the context is exited.

    If multiple coroutines are awaiting messages, the first coroutine to await will
    receive the first message, the second coroutine to await will receive the second
    message, and so on.

    If coroutines are awaiting messages when the context is exited, they will be
    cancelled.
    """

    def __init__(
        self, perpetual_pub_sub: PerpetualPubSub, channel: str, hint: str
    ) -> None:
        """
        Args:
            perpetual_pub_sub: The interface to the actual perpetual pub sub process.
            channel: The channel this is a subscription for
            hint: Used to prefix the uid, which will make tracking down errors easier. Should
                be a short string that identifies the source of the subscription.
        """
        self.perpetual_pub_sub = perpetual_pub_sub
        """The interface to the actual perpetual pub sub process."""

        self.channel = channel
        """The channel this is a subscription for"""

        self.uid: str = f"{hint}-{secrets.token_urlsafe(16)}"
        """A unique identifier for this subscription"""

        self.lock: asyncio.Lock = asyncio.Lock()
        """The lock that ensures that messages are sent in the correct order
        to awaiting coroutines.
        """

        self.exit_event: asyncio.Event = asyncio.Event()
        """An event that is set when the context is exited. This is used to cancel
        awaiting coroutines so that messages are instead directed towards the
        exit context
        """

        self.receive_pipe: Optional[multiprocessing.connection.Connection] = None
        """The pipe to receive messages from the perpetual pub sub process. This is
        set when the context is entered.
        """

        self.send_pipe: Optional[multiprocessing.connection.Connection] = None
        """The pipe that the perpetual pub sub process uses to send messages to this
        subscription. This is set when the context is entered.
        """

        self.background_thread: Optional[threading.Thread] = None
        """The thread which we use for polling the underlying pipe in a cancellable
        manner.
        """

        self.loop: Optional[asyncio.AbstractEventLoop] = None
        """The loop for the main thread. This is set when the context is entered."""

        self.background_thread_poll_event: asyncio.Event = asyncio.Event()
        """The event set by the background thread when a message is available. After
        being set the background thread waits for the background_thread_resume_event.
        Note this is an asyncio event since it's waited on by the main thread.
        """

        self.background_thread_resume_event: threading.Event = threading.Event()
        """The event set to resume the background thread, indicating that a message
        has been read or the shutdown event was set.
        """

        self.background_thread_shutdown_event: threading.Event = threading.Event()
        """The event set to request the background thread shutdown."""

    async def __aenter__(self) -> "PPSSubscription":
        """Enters the context. This will subscribe to the channel and return the
        subscription object.
        """
        self.exit_event.clear()
        self.background_thread_poll_event.clear()
        self.background_thread_resume_event.clear()
        self.background_thread_shutdown_event.clear()
        self.loop = asyncio.get_running_loop()

        receive_pipe, send_pipe = mp.Pipe()
        self.receive_pipe = receive_pipe
        self.send_pipe = send_pipe

        self.perpetual_pub_sub.subscribe_queue.put((self.uid, self.channel, send_pipe))
        self.background_thread = threading.Thread(
            target=_poll_pipe,
            args=[
                self.background_thread_shutdown_event,
                self.receive_pipe,
                self.loop,
                self.background_thread_poll_event,
                self.background_thread_resume_event,
            ],
            daemon=True,
        )
        self.background_thread.start()

        try:
            msg = await self.read(5)
            assert (
                msg == b"ready"
            ), "Expected ready message from perpetual pub sub process"
        except BaseException:
            await self.__aexit__(*sys.exc_info())
            raise

        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        """Exits the context. This will unsubscribe from the channel."""
        if self.send_pipe is None:
            raise RuntimeError("Cannot exit context before entering context")

        if exc_type is not None:
            self._unsafe_close()
            return

        # safe close
        self.exit_event.set()
        async with self.lock:
            self.perpetual_pub_sub.unsubscribe_queue.put((self.uid, self.channel))
            while True:
                bknd_shutdown = asyncio.create_task(
                    self.background_thread_shutdown_event.wait()
                )
                poll = asyncio.create_task(self.background_thread_poll_event.wait())
                try:
                    await asyncio.wait(
                        [bknd_shutdown, poll],
                        timeout=5,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                except asyncio.TimeoutError:
                    # probably can't do any networking as we've been shutdown
                    traceback.print_exc()
                    self._unsafe_close()
                    return

                if bknd_shutdown.done():
                    # background thread has exited before we sent the signal,
                    # meaning that the receive pipe broke, meaning that the
                    # perpetual pub sub process has exited.
                    self._unsafe_close()
                    return

                msg = self.receive_pipe.recv_bytes()
                if msg == b"closed":
                    break
                self.background_thread_poll_event.clear()
                self.background_thread_resume_event.set()

        self.background_thread_shutdown_event.set()
        self.background_thread_resume_event.set()
        self.background_thread.join()

        self.send_pipe.close()
        self.receive_pipe.close()
        self.send_pipe = None
        self.receive_pipe = None
        self.background_thread = None
        self.loop = None

    def _unsafe_close(self) -> None:
        self.exit_event.set()
        self.send_pipe.close()
        self.receive_pipe.close()
        self.background_thread_shutdown_event.set()
        self.background_thread_resume_event.set()
        self.background_thread.join()
        self.send_pipe = None
        self.receive_pipe = None
        self.background_thread = None
        self.loop = None

    def __aiter__(self) -> AsyncIterator[bytes]:
        return self

    async def __anext__(self) -> bytes:
        try:
            return await self.read()
        except asyncio.CancelledError:
            raise StopAsyncIteration

    async def read(self, timeout: Optional[float] = None) -> bytes:
        """Reads the next message from the channel. This will block until a message
        is received, or the timeout is reached. This is cancellable.

        Args:
            timeout (float, None): The timeout in seconds. If None, this will block
                until a message is received.

        Raises:
            asyncio.TimeoutError: If the timeout is reached.
            PPSShutdownException: If the context is exited.
        """
        if self.exit_event.is_set():
            raise PPSShutdownException("Subscription is being removed")

        async with self.lock:
            if self.exit_event.is_set():
                raise PPSShutdownException("Subscription is being removed")
            exit_task = asyncio.create_task(self.exit_event.wait())
            poll_task = asyncio.create_task(self.background_thread_poll_event.wait())

            try:
                await asyncio.wait(
                    [exit_task, poll_task],
                    timeout=timeout,
                    return_when=asyncio.FIRST_COMPLETED,
                )
            except asyncio.TimeoutError:
                exit_task.cancel()
                poll_task.cancel()
                raise

            if exit_task.done():
                poll_task.cancel()
                raise PPSShutdownException("Subscription is being removed")

            exit_task.cancel()

            res = self.receive_pipe.recv_bytes()
            self.background_thread_poll_event.clear()
            self.background_thread_resume_event.set()

            if res == b"closed":
                raise PPSShutdownException("Subscription was forcibly stopped")

            return res


def _poll_pipe(
    background_thread_shutdown_event: threading.Event,
    receive_pipe: multiprocessing.connection.Connection,
    loop: asyncio.AbstractEventLoop,
    background_thread_poll_event: asyncio.Event,
    background_thread_resume_event: threading.Event,
) -> None:
    """The target for a background thread dedicated to polling the underlying pipe
    for read.
    """
    while not background_thread_shutdown_event.is_set():
        try:
            poll_result = receive_pipe.poll(1)
        except OSError:
            background_thread_shutdown_event.set()
            break

        if poll_result:
            if background_thread_shutdown_event.is_set():
                break

            loop.call_soon_threadsafe(background_thread_poll_event.set)
            background_thread_resume_event.wait()
            background_thread_resume_event.clear()


instance: Optional[PerpetualPubSub] = None
"""The global instance of the perpetual pub sub, if its been initialized. This
needs to be carefully forwarded to all child processes, as the perpetual pub sub
instance should only be created in the main process.
"""
