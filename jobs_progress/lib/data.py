from dataclasses import dataclass
from typing import List, Optional

from jobs_progress.lib.packets import JobProgressOutgoingModel


@dataclass
class JobsProgressWatchCoreData:
    job_progress_uid: str
    """The UID of the job whose progress is being watched"""


@dataclass
class JobsProgressWatchTimeoutData:
    last_event_at: float
    """The last time we forwarded an event; we timeout the client if its been
    too long since we've sent an event.
    """

    final_event_at: Optional[float]
    """If we have sent a final event, this is the time we sent that event. We
    timeout the client if it stays connected too long after that event.
    """


@dataclass
class JobsProgressInitialEventsData:
    events: Optional[List[JobProgressOutgoingModel]]
    """The initial events to send to the client, or None if we have already
    sent them
    """


@dataclass
class JobsProgressWatchData:
    core: JobsProgressWatchCoreData
    """The core data for managing the stream"""
    timeout: JobsProgressWatchTimeoutData
    """The timeout data"""
    initial_events: JobsProgressInitialEventsData
    """The initial events"""
