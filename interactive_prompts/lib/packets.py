"""This module provides pydantic models for all the packets transmitted over
the interactive prompt watch websocket, approximately in the order they would normally
be sent.
"""

from typing import Generic, List, Literal, Optional, TypeVar, Union
from pydantic import BaseModel, Field, validator

PacketTypeT = TypeVar("PacketTypeT", bound=str)
PacketDataT = TypeVar("PacketDataT", bound=BaseModel)


class ClientPacket(BaseModel, Generic[PacketTypeT, PacketDataT]):
    """A packet sent from the client to the server"""

    type: PacketTypeT = Field(description="The type of the packet")
    data: PacketDataT = Field(description="The data of the packet")


class ServerFailurePacket(BaseModel, Generic[PacketTypeT, PacketDataT]):
    """A packet sent from the server to the client to indicate something failed"""

    success: Literal[False] = Field(description="Whether the operation was successful")
    type: PacketTypeT = Field(description="The type of the packet")
    uid: str = Field(
        description="A unique ID assigned to this packet, primarily for debugging"
    )
    data: PacketDataT = Field(description="The data of the packet")


class ServerSuccessPacket(BaseModel, Generic[PacketTypeT, PacketDataT]):
    """A packet sent from the server to the client to indicate something succeeded"""

    success: Literal[True] = Field(description="Whether the operation was successful")
    type: PacketTypeT = Field(description="The type of the packet")
    uid: str = Field(
        description="A unique ID assigned to this packet, primarily for debugging"
    )
    data: PacketDataT = Field(description="The data of the packet")


ErrorTypeT = TypeVar("ErrorTypeT")


class ErrorPacketData(BaseModel, Generic[ErrorTypeT]):
    """A packet sent from the server to the client if an error occurs."""

    code: int = Field(description="The error code, interpreted like a status code")
    type: ErrorTypeT = Field(
        description="The type of the error, e.g., unprocessable_entity"
    )
    message: str = Field(description="A human-readable message describing the error")


class AuthRequestPacketData(BaseModel):
    interactive_prompt_uid: str = Field(
        description="The UID of the interactive prompt to watch",
        min_length=1,
        max_length=255,
    )
    jwt: str = Field(
        description="The JWT which proves they are allowed to watch the prompt",
        min_length=2,
    )
    bandwidth: int = Field(
        description="The desired maximum number of events per second to receive",
        ge=1,
        le=5000,
    )
    lookback: float = Field(
        description=(
            "The maximum number of seconds in the past the client wants to receive "
            "events from. For example, for a lookback of 5, if the client is at a "
            "prompt time of 11 seconds, it will still receive live events from a "
            "prompt time of 6 seconds, but not from 5 seconds."
        ),
        ge=0,
        le=10,
    )
    lookahead: float = Field(
        description=(
            "The maximum number of seconds in the future the client wants to receive "
            "events from. For example, for a lookahead of 5, if the client is at a "
            "prompt time of 11 seconds, it will still receive live events from a "
            "prompt time of 16 seconds, but not from 17 seconds."
        ),
        ge=0,
        le=10,
    )


class SyncRequestPacketData(BaseModel): ...


class SyncResponsePacketData(BaseModel):
    receive_timestamp: float = Field(
        description=(
            "The prompt time that the client received the sync request at, in seconds "
            "from the start. The client can start their prompt time at a negative "
            "value in order to allow time for the initial handshake"
        )
    )

    transmit_timestamp: float = Field(
        description=(
            "The prompt time that the client sent back the sync response at, in seconds "
            "from the start. The client can start their prompt time at a negative "
            "value in order to allow time for the initial handshake This must be at least "
            "the receive_timestamp is primarily used when the client purposely delays their "
            "response."
        )
    )

    @validator("transmit_timestamp")
    def transmit_timestamp_must_be_at_least_receive_timestamp(
        cls, transmit_timestamp: float, values: dict, **kwargs
    ) -> float:
        if (
            "receive_timestamp" in values
            and transmit_timestamp < values["receive_timestamp"]
        ):
            raise ValueError("transmit_timestamp must be at least receive_timestamp")
        return transmit_timestamp


class AuthResponseSuccessPacketData(BaseModel): ...


class EventBatchPacketDataItemJoinData(BaseModel):
    name: str = Field(description="The name of the user")


class EventBatchPacketDataItemLeaveData(BaseModel):
    name: str = Field(description="The name of the user")


class EventBatchPacketDataItemLikeData(BaseModel): ...


class EventBatchPacketDataItemNumericPromptResponseData(BaseModel):
    rating: int = Field(description="the rating given by the user", ge=0)


class EventBatchPacketDataItemPressPromptStartResponseData(BaseModel): ...


class EventBatchPacketDataItemPressPromptEndResponseData(BaseModel): ...


class EventBatchPacketDataItemColorPromptResponseData(BaseModel):
    index: int = Field(description="the index of the color selected by the user", ge=0)


class EventBatchPacketDataItemWordPromptResponseData(BaseModel):
    index: int = Field(description="the index of the word selected by the user", ge=0)


EventBatchPacketDataItemTypeT = Literal[
    "join",
    "leave",
    "like",
    "numeric_prompt_response",
    "press_prompt_start_response",
    "press_prompt_end_response",
    "color_prompt_response",
    "word_prompt_response",
]

EventBatchPacketDataItemDataT = Union[  # sensitive to order as it takes the first match
    EventBatchPacketDataItemJoinData,
    EventBatchPacketDataItemLeaveData,
    EventBatchPacketDataItemNumericPromptResponseData,
    EventBatchPacketDataItemColorPromptResponseData,
    EventBatchPacketDataItemWordPromptResponseData,
    EventBatchPacketDataItemLikeData,
    EventBatchPacketDataItemPressPromptStartResponseData,
    EventBatchPacketDataItemPressPromptEndResponseData,
]


class ImageRef(BaseModel):
    uid: str = Field(description="The uid of the image file")
    jwt: str = Field(description="A jwt that can be used to access the image")


class EventBatchPacketDataItem(BaseModel):
    uid: str = Field(description="the unique id for this event")
    user_sub: Optional[str] = Field(
        description=(
            "acts as a unique identifier for the user associated with the "
            "event. may omitted by the server arbitrarily, but most notably "
            "for synthetic events (i.e., events released simply to sync the client)"
        )
    )
    session_uid: Optional[str] = Field(
        description=(
            "acts as a unique identifier for the session associated with the "
            "event. may omitted by the server arbitrarily, but most notably "
            "for synthetic events (i.e., events released simply to sync the client)"
        )
    )
    evtype: EventBatchPacketDataItemTypeT = Field(description="the type of event")
    prompt_time: float = Field(
        description=(
            "the prompt clock time of when the event occurred, in fractional seconds. "
            "the client MUST NOT assume events are received from the server in order. if "
            "the event is prior to the clients prompt time, the client should present "
            "it as soon as possible, otherwise, it should be presented at the appropriate "
            "prompt time."
        ),
    )
    icon: Optional[ImageRef] = Field(
        description=(
            "If an icon is associated with this event, such as the user's profile picture, "
            "then a reference to that image."
        )
    )
    data: EventBatchPacketDataItemDataT = Field(
        description="the data for the event; depends on the type"
    )


class EventBatchPacketData(BaseModel):
    events: List[EventBatchPacketDataItem] = Field(
        description="the events in the batch",
        min_length=1,
    )


class LatencyDetectionPacketData(BaseModel):
    expected_receive_prompt_time: float = Field(
        description=(
            "What the server expects the clients prompt time "
            "is at the moment it receives this packet. If there "
            "is a significant disagreement, the client must reconnect, "
            "potentially with a new bandwidth"
        )
    )


AuthRequestPacket = ClientPacket[Literal["authorize"], AuthRequestPacketData]
"""The first packet over the connection."""

AUTH_REQUEST_RESPONSE_ERROR_TYPES = Literal[
    "unprocessable_entity", "forbidden", "not_found"
]
AuthRequestResponseErrorPacket = ServerFailurePacket[
    Literal["error"],
    ErrorPacketData[AUTH_REQUEST_RESPONSE_ERROR_TYPES],
]
"""The response from the server to the auth request packet if it was invalid."""

SyncRequestPacket = ServerSuccessPacket[Literal["sync_request"], SyncRequestPacketData]
"""The response from the server to the client when an auth request packet is
accepted, which begins the process of the server syncing a prompt time clock with
the client.
"""

SyncResponsePacket = ClientPacket[Literal["sync_response"], SyncResponsePacketData]
"""The response from the client to the servers sync request packet."""

SYNC_RESPONSE_RESPONSE_ERROR_TYPES = Literal["unprocessable_entity"]
SyncResponseResponseErrorPacket = ServerFailurePacket[
    Literal["error"], ErrorPacketData[SYNC_RESPONSE_RESPONSE_ERROR_TYPES]
]
"""The response from the server to the clients SyncResponse packet if it was malformed"""

AuthResponseSuccessPacket = ServerSuccessPacket[
    Literal["auth_response"], AuthResponseSuccessPacketData
]
"""The response from the server to the SyncResponsePacket when it was accepted"""


EventBatchPacket = ServerSuccessPacket[Literal["event_batch"], EventBatchPacketData]
"""The server periodically sends event batches to the client for events which
occurred very recently within the time interval requested, where the time interval
is moving forward at 1s/s. The client should present events at or before its
actual prompt time as soon as possible, and then the future ones at the
appropriate prompt time.
"""

LatencyDetectionPacket = ServerSuccessPacket[
    Literal["latency_detection"], LatencyDetectionPacketData
]
"""The server periodically sends latency detection packets to the client to
allow it to detect network interruptions and reconnect with a new bandwidth if
necessary. Since the websocket is over tcp, the client will get packets in
order, and network congestion will show up as a delay in packets. Hence, the
client can detect network interruptions by comparing the prompt time it was at
when it received the packet with the prompt time it expects to be at when it
receives the packet. These clocks have already been synced, so they will normally
be within a few hundred milliseconds of each other.
"""
