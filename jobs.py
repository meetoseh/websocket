import redis.asyncio
import json
import time
from typing import Literal, Optional, Union, cast as typing_cast, Awaitable
from typing_extensions import TypedDict

from redis_helpers.push_job_progress import (
    ensure_push_job_progress_script_exists,
    push_job_progress,
)
from redis_helpers.run_with_prep import run_with_prep


class Job(TypedDict):
    """describes a job dictionary"""

    name: str
    """the name of the job which corresponds to the import path in the jobs module
    e.g., 'runners.charge' corresponds to the excecute function in jobs/charge.py
    relative to the jobs root directory
    """
    kwargs: dict
    """the keyword arguments to pass to the job; must be json serializable
    the jobs will automatically be sent the integrations and graceful death handler
    """
    queued_at: float
    """the time when the job was enqueued"""


class JobProgressIndicatorBar(TypedDict):
    """describes a hint that a progress bar indicator should be displayed"""

    type: Literal["bar"]
    """discriminative field"""
    at: Union[int, float]
    """How much progress has been made, out of `of`"""
    of: Union[int, float]
    """How much progress is needed to complete the job or step"""


class JobProgressIndicatorSpinner(TypedDict):
    """describes a hint that a progress spinner indicator should be displayed"""

    type: Literal["spinner"]
    """discriminative field"""


class JobProgressIndicatorFinal(TypedDict):
    """describes a hint that no more messages will be sent"""

    type: Literal["final"]
    """discriminative field"""


JobProgressIndicator = Union[
    JobProgressIndicatorBar, JobProgressIndicatorSpinner, JobProgressIndicatorFinal
]

JobProgressType = Literal[
    "queued", "started", "bounce", "progress", "failed", "succeeded"
]


class JobProgress(TypedDict):
    """describes a job progress message"""

    type: JobProgressType
    """the type of progress message"""
    message: str
    """the message to display to the user"""
    indicator: Optional[JobProgressIndicator]
    """a hint about how to display the progress or None if no indicator
    should be displayed
    """
    occurred_at: float
    """the time when the progress message was created"""


class Jobs:
    """interface for queueing and retreiving jobs
    acts as an asynchronous context manager"""

    def __init__(self, conn: redis.asyncio.Redis) -> None:
        """initializes a new interface for queueing and retreiving jobs

        Args:
            conn (redis.asyncio.Redis): the redis connection to use
        """
        self.conn: redis.asyncio.Redis = conn
        """the redis connection containing the jobs queue"""

        self.queue_key: bytes = b"jobs:hot"
        """the key for the list in redis"""

    async def __aenter__(self) -> "Jobs":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        pass

    async def enqueue(self, name: str, **kwargs) -> None:
        """queues the job with the given name and key word arguments

        the job is run as soon as possible, and is not retried regardless of success.

        Args:
            name (str): the name of the job which corresponds to the import path in the jobs module
                e.g., 'runners.charge' corresponds to the execute function in jobs/charge.py
                relative to the jobs root directory
            kwargs (dict): the keyword arguments to pass to the job; must be json serializable
                the jobs will automatically be sent the integrations and graceful death handler
        """
        await self.enqueue_in_pipe(self.conn, name, **kwargs)

    async def enqueue_with_progress(
        self, name: str, progress_uid: str, /, **kwargs
    ) -> None:
        """queues the job with the given name and key word arguments, and also
        pushes the initial progress message to the progress event list with the
        given uid.

        the job is run as soon as possible, and is not retried regardless of success.

        the job is not passed the progress uid automatically, but will need it

        Args:
            name (str): the name of the job which corresponds to the import path in the jobs module
                e.g., 'runners.charge' corresponds to the execute function in jobs/charge.py
                relative to the jobs root directory
            progress_uid (str): the unique identifier for the progress event list
            kwargs (dict): the keyword arguments to pass to the job; must be json serializable
                the jobs will automatically be sent the integrations and graceful death handler
        """

        conn = self.conn
        progress: JobProgress = {
            "type": "queued",
            "message": "waiting for an available worker",
            "indicator": {"type": "spinner"},
            "occurred_at": time.time(),
        }

        async def _prepare(force: bool):
            await self.prepare_progress(conn, force=force)

        async def _execute():
            async with conn.pipeline() as pipe:
                pipe.multi()
                await self.push_progress_in_pipe(
                    pipe, progress_uid=progress_uid, progress=progress
                )
                await self.enqueue_in_pipe(pipe, name, **kwargs)
                await pipe.execute()

        await run_with_prep(_prepare, _execute)

    async def enqueue_in_pipe(
        self, pipe: redis.asyncio.Redis, name: str, **kwargs
    ) -> None:
        """queues the job with the given name and key word arguments, using the
        specified redis connection. This is primarily for batching jobs or for
        performing other redis operations in the same transaction.

        the job is run as soon as possible, and is not retried regardless of success.

        Args:
            pipe (redis.asyncio.Redis): the redis connection to use
            name (str): the name of the job which corresponds to the import path in the jobs module
                e.g., 'runners.example' corresponds to the execute function in runners/example.py
                relative to the jobs root directory
            kwargs (dict): the keyword arguments to pass to the job; must be json serializable
                the jobs will automatically be sent the integrations and graceful death handler
        """
        job = {"name": name, "kwargs": kwargs, "queued_at": time.time()}
        job_serd = json.dumps(job)
        await pipe.rpush(self.queue_key, job_serd.encode("utf-8"))  # type: ignore

    async def prepare_progress(self, conn: redis.asyncio.Redis, *, force: bool) -> None:
        """Ensures the required scripts for `push_progress_in_pipe` are loaded.

        Args:
            conn (redis.asyncio.Redis): the redis connection to use
            force (bool): whether to force the script to be loaded or verified to
                be loaded even if we have done so recently
        """
        await ensure_push_job_progress_script_exists(conn, force=force)

    async def push_progress_in_pipe(
        self, pipe: redis.asyncio.Redis, progress_uid: str, progress: JobProgress
    ) -> None:
        """Pushes the given job progress message to the progress event list with the
        given uid. This is primarily for batching progress messages or for
        performing other redis operations in the same transaction.

        This may fail with NOSCRIPT if `prepare_progress` has not been called or
        the script was deleted.
        """
        await push_job_progress(
            pipe, progress_uid.encode("utf-8"), json.dumps(progress).encode("utf-8")
        )

    async def retrieve(self, timeout: int) -> Optional[Job]:
        """blocking retrieve of the oldest job in the queue, if there is one

        Args:
            timeout (float): maximum time in seconds to wait for a job to be enqueued

        Returns:
            (Job, None): The oldest job, if there is one
        """
        response = await typing_cast(
            Awaitable[list],
            self.conn.blpop([self.queue_key], timeout=timeout),
        )
        if response is None:
            return None
        job_serd_and_encoded: bytes = response[1]
        print(repr(job_serd_and_encoded))
        job_serd = job_serd_and_encoded.decode("utf-8")
        print(repr(job_serd))
        job = json.loads(job_serd)
        return job
