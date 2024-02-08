from typing import Annotated, List, Literal, Optional, Union
from pydantic import BaseModel, Field, StringConstraints
from interactive_prompts.lib.packets import (
    ClientPacket,
    ServerFailurePacket,
    ServerSuccessPacket,
    ErrorPacketData,
    AuthResponseSuccessPacketData,
    AuthResponseSuccessPacket,
    AuthRequestResponseErrorPacket as AuthResponseErrorPacket,
    AUTH_REQUEST_RESPONSE_ERROR_TYPES as AUTH_RESPONSE_ERROR_TYPES,
)
from jobs import JobProgressType


class AuthRequestPacketData(BaseModel):
    job_uid: Annotated[str, StringConstraints(min_length=2, max_length=255)] = Field(
        description="The job progress UID whose events stream is being requested"
    )
    jwt: Annotated[str, StringConstraints(min_length=2, max_length=4096)] = Field(
        description="The JWT to authenticate the request"
    )


AuthRequestPacket = ClientPacket[Literal["authorize"], AuthRequestPacketData]
"""The first packet over the connection."""

# Returns AuthResponseSuccessPacket or AuthResponseErrorPacket


class JobProgressIndicatorBarModel(BaseModel):
    type: Literal["bar"] = Field("bar", description="discriminative field")
    at: float = Field(description="How much progress has been made, out of `of`")
    of: float = Field(
        description="How much progress is needed to complete the job or step"
    )


class JobProgressIndicatorSpinnerModel(BaseModel):
    type: Literal["spinner"] = Field("spinner", description="discriminative field")


class JobProgressIndicatorFinalModel(BaseModel):
    type: Literal["final"] = Field("final", description="discriminative field")


JobProgressIndicatorModel = Union[
    JobProgressIndicatorBarModel,
    JobProgressIndicatorSpinnerModel,
    JobProgressIndicatorFinalModel,
]


class JobProgressModel(BaseModel):
    type: JobProgressType = Field(description="the type of progress message")
    message: str = Field(description="the message to display to the user")
    indicator: Optional[JobProgressIndicatorModel] = Field(
        description=(
            "a hint about how to display the progress "
            "or null if no indicator should be displayed"
        )
    )
    occurred_at: float = Field(
        description=(
            "the time when the progress message was created in seconds since the epoch. "
            "events are provided in the order they occurred, but due to clock "
            "drift, this may not result in non-decreasing occurred_at values"
        )
    )


class EventBatchPacketData(BaseModel):
    events: List[JobProgressModel] = Field(description="the events in the batch")


EventBatchPacket = ServerSuccessPacket[Literal["event_batch"], EventBatchPacketData]

GenericServerErrorTypes = Literal["internal_server_error", "service_unavailable"]

ServerGenericErrorPacket = ServerFailurePacket[
    Literal["server_error"], ErrorPacketData[GenericServerErrorTypes]
]

__all__ = (
    "ClientPacket",
    "ServerFailurePacket",
    "ServerSuccessPacket",
    "ErrorPacketData",
    "AuthResponseSuccessPacketData",
    "AuthResponseSuccessPacket",
    "AuthResponseErrorPacket",
    "AUTH_RESPONSE_ERROR_TYPES",
    "AuthRequestPacketData",
    "AuthRequestPacket",
    "EventBatchPacketData",
    "EventBatchPacket",
    "GenericServerErrorTypes",
    "ServerGenericErrorPacket",
)
