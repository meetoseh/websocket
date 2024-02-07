"""Standard responses and requests"""

from pydantic import BaseModel, Field, validator
from typing import Any, Dict, Generic, TypeVar, Optional, Literal, Union
from fastapi.responses import Response

TypeT = TypeVar("TypeT")


class StandardErrorResponse(BaseModel, Generic[TypeT]):
    type: TypeT = Field(
        title="Type",
        description="the type of error that occurred",
    )

    message: str = Field(title="Message", description="a human readable error message")

    markdown: str = Field(
        title="Markdown", description="markdown formatted error message"
    )

    @validator("markdown", pre=True, always=True)
    def set_markdown(cls, v, values, **kwargs):
        if v is not None:
            return v
        return values["message"]

    def __init__(self, *, type: TypeT, message: str, markdown: Optional[str] = None):
        super().__init__(type=type, message=message, markdown=markdown)


ERROR_401_TYPE = Literal["not_set", "bad_format"]
"""the standard error type for a 401 response"""

ERROR_403_TYPE = Literal["invalid"]
"""the standard error type for a 403 response"""

STANDARD_ERRORS_BY_CODE: Dict[Union[int, str], Dict[str, Any]] = {
    "401": {
        "description": "if authorization is not set",
        "model": StandardErrorResponse[ERROR_401_TYPE],
    },
    "403": {
        "description": "if the authorization is invalid",
        "model": StandardErrorResponse[ERROR_403_TYPE],
    },
}
"""error responses common to nearly every endpoint"""

AUTHORIZATION_NOT_SET = Response(
    content=StandardErrorResponse[ERROR_401_TYPE](
        type="not_set", message="authorization header not set"
    ).model_dump_json(),
    headers={"Content-Type": "application/json; charset=utf-8"},
    status_code=401,
)
"""the response if an expected authorization header is missing"""

AUTHORIZATION_INVALID_PREFIX = Response(
    content=StandardErrorResponse[ERROR_401_TYPE](
        type="bad_format",
        message="authorization header should start with 'bearer '",
    ).model_dump_json(),
    headers={"Content-Type": "application/json; charset=utf-8"},
    status_code=401,
)
"""the response if the authorization header is missing the expected prefix"""

AUTHORIZATION_UNKNOWN_TOKEN = Response(
    content=StandardErrorResponse[ERROR_403_TYPE](
        type="invalid",
        message="token is invalid",
    ).model_dump_json(),
    headers={"Content-Type": "application/json; charset=utf-8"},
    status_code=403,
)
"""the response if the token in the authorization header is not recognized"""
