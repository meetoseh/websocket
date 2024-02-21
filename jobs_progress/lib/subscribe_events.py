from typing import List, Optional
import redis.asyncio
import redis.asyncio.client
from pydantic import TypeAdapter
from jobs_progress.lib.packets import JobProgressIncomingModel


events_adapter = TypeAdapter(List[JobProgressIncomingModel])


class EventSubscription:
    def __init__(self, *, job_progress_uid: str, conn: redis.asyncio.Redis) -> None:
        self.job_progress_uid = job_progress_uid
        """The uid of the job we are watching"""

        self.conn = conn
        """The redis instance used to subscribe to events"""

        self.pubsub: Optional[redis.asyncio.client.PubSub] = None
        """The active pubsub instance, if we've been entered, else None"""

        self.reading = False
        """If we have an ongoing read request"""

    async def __aenter__(self) -> "EventSubscription":
        assert self.pubsub is None, "non-reentrant __aenter__"

        self.pubsub = self.conn.pubsub()
        await self.pubsub.subscribe(
            f"ps:jobs:progress:{self.job_progress_uid}".encode("utf-8")
        )
        return self

    async def __aexit__(self, *args) -> None:
        pubsub = self.pubsub
        self.pubsub = None

        if pubsub is None:
            return

        try:
            await pubsub.unsubscribe()
        except Exception:
            pass

        await pubsub.aclose()

    async def next(self, *, timeout: int) -> List[JobProgressIncomingModel]:
        pubsub = self.pubsub
        assert pubsub is not None, "not entered"
        assert not self.reading, "non-reentrant next"

        self.reading = True
        try:
            message = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=timeout
            )
            if message is None:
                return []

            return events_adapter.validate_json(message["data"])
        finally:
            self.reading = False
