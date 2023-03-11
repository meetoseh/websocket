"""This module describes the transient data that is maintained by the
websocket server for a connected client
"""

from dataclasses import dataclass
import time


@dataclass
class InteractivePromptWatchCoreData:
    """The core data required for the server to stream events to the client."""

    # region: prompt time
    rate: float
    """The rate at which the prompt time is advancing in seconds per second.
    This is currently always one, but allows for better parity with the standard
    calculation for syncing clocks.
    """

    prompt_time_perf_counter_base: float
    """The result from time.perf_counter() that should result in the prompt_time
    time equal to prompt_time_base
    """

    prompt_time_base: float
    """The base prompt time after which the rate is applied to get the true
    prompt time. For example, if the prompt_time_base is 5, then the
    prompt_time is 5 + delta_time * rate
    """

    round_trip_delay: float
    """The time it takes for a packet to travel from server to the client and back,
    in fractional seconds.
    """

    @property
    def prompt_time(self) -> float:
        """The server prompt time. This is the time that, if we were to send
        an event at this prompt time to the client right now, they would
        receive it exactly when they needed it, assuming that the latency is
        the same in both directions.
        """
        real_time_delta = time.perf_counter() - self.prompt_time_perf_counter_base
        prompt_time_delta = real_time_delta * self.rate

        latency = self.round_trip_delay / 2
        prompt_time_during_latency = latency * self.rate

        return self.prompt_time_base + prompt_time_delta + prompt_time_during_latency

    # endregion: prompt time

    interactive_prompt_uid: str
    """The uid of the interactive prompt"""

    prompt_duration: float
    """how long the prompt lasts, in seconds"""

    bandwidth: int
    """The clients desired number of events per second"""

    lookback: float
    """The maximum number of seconds before the `prompt_time` for live events
    in the stream.
    """

    lookahead: float
    """The maximum number of seconds after the `prompt_time` for live events
    in the stream.

    Combining `lookback`, `prompt_time`, and `lookahead` gives the range
    `(prompt_time - lookback, prompt_time + lookback)`. When a new live
    event occurs, it is only sent to the client if its `prompt_time` is
    within this range.
    """


@dataclass
class InteractivePromptWatchLatencyDetectionData:
    """Information required to regularly send latency detection packets to the client"""

    next_at: float
    """The prompt time when the next latency detection packet should be sent"""


@dataclass
class InteractivePromptWatchData:
    """The data required for the server to stream events to the client."""

    core: InteractivePromptWatchCoreData
    latency_detection: InteractivePromptWatchLatencyDetectionData
