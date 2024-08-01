"""This module provides pydantic models for all the packets transmitted over
the interactive prompt watch websocket, approximately in the order they would normally
be sent.
"""

from typing import Any, Generic, List, Literal, Optional, TypeVar, Union
from pydantic import BaseModel, Field

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
    jwt: str = Field(
        description="The JWT which describes and authorizes the client to see a journal chat",
        min_length=2,
    )


class AuthResponseSuccessPacketData(BaseModel): ...


class EventBatchPacketDataItemDataThinkingBar(BaseModel):
    type: Literal["thinking-bar"] = Field(
        description="A hint that the server is working and a progress bar could be used"
    )
    at: int = Field(
        description="The progress is being expressed as a fraction and this is the numerator"
    )
    of: int = Field(
        description="The progress is being expressed as a fraction and this is the denominator"
    )
    message: str = Field(
        description="Short text for what the server is doing, e.g., 'Waiting for worker'"
    )
    detail: Optional[str] = Field(
        None,
        description="Longer text for what the server is doing, e.g., 'Waiting in priority queue because you have Oseh+'",
    )


class EventBatchPacketDataItemDataThinkingSpinner(BaseModel):
    type: Literal["thinking-spinner"] = Field(
        description="A hint that the server is working and a spinner could be used"
    )
    message: str = Field(
        description="Short text for what the server is doing, e.g., 'Waiting for worker'"
    )
    detail: Optional[str] = Field(
        None,
        description="Longer text for what the server is doing, e.g., 'Waiting in priority queue because you have Oseh+'",
    )


class EventBatchPacketDataItemDataError(BaseModel):
    type: Literal["error"] = Field(
        description="A hint that the server encountered an error"
    )
    message: str = Field(
        description="Short text for what went wrong, e.g., 'Internal failure'"
    )
    detail: Optional[str] = Field(
        None,
        description="Longer text for what went wrong, e.g., 'Oseh is down for maintenance'",
    )


class SegmentDataMutation(BaseModel):
    key: List[Union[int, str]] = Field(
        description="The path to the key to insert (if it does not exist) or update (if it does exist). An empty key list means replace the root object."
    )
    value: Any = Field(description="The value to set at the key")


class SegmentData(BaseModel):
    mutations: List[SegmentDataMutation] = Field(
        description="The mutations to apply to the chat data. After applying all "
        "these mutations, all integrity checks should pass"
    )


class EventBatchPacketDataItemDataChat(BaseModel):
    type: Literal["chat"] = Field(
        description="Describes a mutation to the journal chat state"
    )
    encrypted_segment_data: str = Field(
        description="a SegmentData, json stringified, then Fernet encrypted, then urlsafe base64 encoded"
    )
    more: bool = Field(
        description="Whether there are more chat events to come in this batch"
    )


EventBatchPacketDataItem = Union[
    EventBatchPacketDataItemDataThinkingBar,
    EventBatchPacketDataItemDataThinkingSpinner,
    EventBatchPacketDataItemDataError,
    EventBatchPacketDataItemDataChat,
]


class EventBatchPacketData(BaseModel):
    events: List[EventBatchPacketDataItem] = Field(
        description="the events in the batch",
        min_length=1,
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

AuthResponseSuccessPacket = ServerSuccessPacket[
    Literal["auth_response"], AuthResponseSuccessPacketData
]
"""The response from the server to the AuthRequestPacket when it was accepted"""


EventBatchPacket = ServerSuccessPacket[Literal["event_batch"], EventBatchPacketData]
"""The server periodically sends event batches to the client for events that
occurred within the journal chat. The server may combine or break apart "real"
events, so these synthetic events are not deterministic.
"""
