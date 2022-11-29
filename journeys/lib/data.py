"""This module describes the transient data that is maintained by the
websocket server for a connected client
"""

from dataclasses import dataclass
import time


@dataclass
class JourneyWatchCoreData:
    """The core data required for the server to stream events to the client."""

    # region: journey time
    rate: float
    """The rate at which the journey time is advancing in seconds per second.
    This is currently always one, but allows for better parity with the standard
    calculation for syncing clocks.
    """

    journey_time_perf_counter_base: float
    """The result from time.perf_counter() that should result in the journey
    time equal to journey_time_base
    """

    journey_time_base: float
    """The base journey time after which the rate is applied to get the true
    journey time. For example, if the journey_time_base is 5, then the
    journey_time is 5 + delta_time * rate
    """

    round_trip_delay: float
    """The time it takes for a packet to travel from server to the client and back,
    in fractional seconds.
    """

    @property
    def journey_time(self) -> float:
        """The server journey time. This is the time that, if we were to send
        an event at this journey time to the client right now, they would
        receive it exactly when they needed it, assuming that the latency is
        the same in both directions.
        """
        real_time_delta = time.perf_counter() - self.journey_time_perf_counter_base
        journey_time_delta = real_time_delta * self.rate

        latency = self.round_trip_delay / 2
        journey_time_during_latency = latency * self.rate

        return self.journey_time_base + journey_time_delta + journey_time_during_latency

    # endregion: journey time

    journey_uid: str
    """The uid of the journey"""

    journey_duration: float
    """how long the journey is, in seconds"""

    bandwidth: int
    """The clients desired number of events per second"""

    lookback: float
    """The maximum number of seconds before the `journey_time` for live events
    in the stream.
    """

    lookahead: float
    """The maximum number of seconds after the `journey_time` for live events
    in the stream.

    Combining `lookback`, `journey_time`, and `lookahead` gives the range
    `(journey_time - lookback, journey_time + lookback)`. When a new live
    event occurs, it is only sent to the client if its `journey_time` is
    within this range.
    """


@dataclass
class JourneyWatchLatencyDetectionData:
    """Information required to regularly send latency detection packets to the client"""

    next_at: float
    """The journey time when the next latency detection packet should be sent"""


@dataclass
class JourneyWatchData:
    """The data required for the server to stream events to the client."""

    core: JourneyWatchCoreData
    latency_detection: JourneyWatchLatencyDetectionData
