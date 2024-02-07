"""This module assists with loading and caching the meta data for an interactive prompt."""

import time
from typing import NoReturn, Optional
from itgs import Itgs
from pydantic import BaseModel, Field
import perpetual_pub_sub as pps


class InteractivePromptMeta(BaseModel):
    """Cacheable information about the interactive prompt, such as its duration"""

    uid: str = Field(description="The UID of the interactive prompt")
    duration_seconds: float = Field(
        description="The duration of the interactive prompt, in seconds"
    )


async def get_interactive_prompt_meta_from_cache(
    itgs: Itgs, interactive_prompt_uid: str
) -> Optional[InteractivePromptMeta]:
    """Gets the cached interactive prompt meta information for the prompt with
    the given uid, if available, otherwise returns None
    """
    local_cache = await itgs.local_cache()
    raw = local_cache.get(
        f"interactive_prompts:{interactive_prompt_uid}:meta".encode("utf-8")
    )
    if raw is None:
        return None
    return InteractivePromptMeta.parse_raw(raw, content_type="application/json")


async def set_cached_interactive_prompt_meta(
    itgs: Itgs, interactive_prompt_uid: str, meta: InteractivePromptMeta
) -> None:
    """Caches the given meta information for the given interactive prompt"""
    local_cache = await itgs.local_cache()
    local_cache.set(
        f"interactive_prompts:{interactive_prompt_uid}:meta".encode("utf-8"),
        meta.model_dump_json().encode("utf-8"),
        expire=60 * 60 * 24,
    )


async def get_interactive_prompt_meta_from_db(
    itgs: Itgs, interactive_prompt_uid: str
) -> Optional[InteractivePromptMeta]:
    """Fetches the meta information on the interactive prompt with the given uid from the
    database, if available, otherwise returns None
    """
    conn = await itgs.conn()
    cursor = conn.cursor("none")
    response = await cursor.execute(
        "SELECT duration_seconds FROM interactive_prompts WHERE uid=?",
        (interactive_prompt_uid,),
    )
    if not response.results:
        return None
    return InteractivePromptMeta(
        uid=interactive_prompt_uid, duration_seconds=response.results[0][0]
    )


async def get_interactive_prompt_meta(
    itgs: Itgs, interactive_prompt_uid: str
) -> Optional[InteractivePromptMeta]:
    """Gets the meta information on the interactive prompt with the given uid, if available,
    otherwise returns None. This will attempt to load from the cache, and otherwise
    will fill the cache.
    """
    meta = await get_interactive_prompt_meta_from_cache(itgs, interactive_prompt_uid)
    if meta is not None:
        return meta
    meta = await get_interactive_prompt_meta_from_db(itgs, interactive_prompt_uid)
    if meta is not None:
        await set_cached_interactive_prompt_meta(itgs, interactive_prompt_uid, meta)
    return meta


class InteractivePromptMetaPurgePubSubMessage(BaseModel):
    interactive_prompt_uid: str = Field(
        description="The UID of the interactive prompt to purge the meta information for"
    )
    min_checked_at: float = Field(
        description=(
            "When this purge was triggered; it's not necessary to purge information "
            "that was cached after this time"
        )
    )


async def purge_interactive_prompt_meta(
    itgs: Itgs, interactive_prompt_uid: str
) -> None:
    """Purges any cached interactive prompt meta information on the interactive
    prompt with the given uid from all instances, forcing it to be reloaded from
    the database. This should be called if the interactive prompt meta information has been
    updated in the database

    Args:
        itgs (Itgs): The integrations to (re)use
        interactive_prompt_uid (str): The interactive prompt uid to purge
    """
    redis = await itgs.redis()
    await redis.publish(
        "ps:interactive_prompts:meta:purge".encode("ascii"),
        InteractivePromptMetaPurgePubSubMessage(
            interactive_prompt_uid=interactive_prompt_uid,
            min_checked_at=time.time(),
        )
        .model_dump_json()
        .encode("utf-8"),
    )


async def purge_interactive_prompt_meta_loop() -> NoReturn:
    """This loop will listen for purge requests from
    ps:interactive_prompts:meta:purge and will purge the interactive prompt meta
    information from our local cache, ensuring it gets reloaded from the nearest
    non-local cache or source. It runs continuously in the background and is
    invoked by the admin interactive prompt update endpoint(s) as if via the
    purge_interactive_prompt_meta function
    """
    async with pps.PPSSubscription(
        pps.instance,
        "ps:interactive_prompts:meta:purge",
        hint="interactive_prompt_meta",
    ) as sub:
        async for raw_data in sub:
            message = InteractivePromptMetaPurgePubSubMessage.parse_raw(
                raw_data, content_type="application/json"
            )
            async with Itgs() as itgs:
                local_cache = await itgs.local_cache()
                local_cache.delete(
                    f"interactive_prompts:{message.interactive_prompt_uid}:meta".encode(
                        "utf-8"
                    )
                )
