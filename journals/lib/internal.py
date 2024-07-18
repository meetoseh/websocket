from pydantic import BaseModel, Field, TypeAdapter
from typing import cast, Literal
from journals.lib.packets import (
    SegmentDataMutation,
    EventBatchPacketDataItemDataThinkingBar,
    EventBatchPacketDataItemDataThinkingSpinner,
    EventBatchPacketDataItemDataError,
)
from typing import List, Union


class JournalChatRedisPacketMutations(BaseModel):
    counter: int = Field()
    type: Literal["mutations"] = Field()
    mutations: List[SegmentDataMutation] = Field(min_length=1)
    more: bool = Field()


class JournalChatRedisPacketPassthrough(BaseModel):
    counter: int = Field()
    type: Literal["passthrough"] = Field()
    event: Union[
        EventBatchPacketDataItemDataThinkingBar,
        EventBatchPacketDataItemDataThinkingSpinner,
        EventBatchPacketDataItemDataError,
    ] = Field()


JournalChatRedisPacket = Union[
    JournalChatRedisPacketMutations, JournalChatRedisPacketPassthrough
]
journal_chat_redis_packet_adapter = cast(
    TypeAdapter[JournalChatRedisPacket], TypeAdapter(JournalChatRedisPacket)
)
