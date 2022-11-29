"""This module assists with loading and caching the meta data for a journey."""


from typing import Optional
from itgs import Itgs
from pydantic import BaseModel, Field


class JourneyMeta(BaseModel):
    """Cacheable information about the journey, such as its duration"""

    uid: str = Field(description="The UID of the journey")
    duration_seconds: float = Field(
        description="The duration of the journey, in seconds"
    )


async def get_journey_meta_from_cache(
    itgs: Itgs, journey_uid: str
) -> Optional[JourneyMeta]:
    """Gets the cached journey meta information for the journey with
    the given uid, if available, otherwise returns None
    """
    local_cache = await itgs.local_cache()
    raw = local_cache.get(f"journeys:{journey_uid}:meta")
    if raw is None:
        return None
    return JourneyMeta.parse_raw(raw, content_type="application/json")


async def set_cached_journey_meta(
    itgs: Itgs, journey_uid: str, meta: JourneyMeta
) -> None:
    """Caches the given meta information for the given journey"""
    local_cache = await itgs.local_cache()
    local_cache.set(
        f"journeys:{journey_uid}:meta", meta.json().encode("utf-8"), expire=60 * 60 * 24
    )


async def get_journey_meta_from_db(
    itgs: Itgs, journey_uid: str
) -> Optional[JourneyMeta]:
    """Fetches the meta information on the journey with the given uid from the
    database, if available, otherwise returns None
    """
    conn = await itgs.conn()
    cursor = conn.cursor("none")
    response = await cursor.execute(
        """
        SELECT
            content_files.duration_seconds
        FROM content_files
        WHERE
            EXISTS (
                SELECT 1 FROM journeys
                WHERE journeys.uid = ?
                  AND journeys.audio_content_file_id = content_files.id
            )
        """,
        (journey_uid,),
    )
    if not response.results:
        return None
    return JourneyMeta(uid=journey_uid, duration_seconds=response.results[0][0])


async def get_journey_meta(itgs: Itgs, journey_uid: str) -> Optional[JourneyMeta]:
    """Gets the meta information on the journey with the given uid, if available,
    otherwise returns None. This will attempt to load from the cache, and otherwise
    will fill the cache.
    """
    meta = await get_journey_meta_from_cache(itgs, journey_uid)
    if meta is not None:
        return meta
    meta = await get_journey_meta_from_db(itgs, journey_uid)
    if meta is not None:
        await set_cached_journey_meta(itgs, journey_uid, meta)
    return meta
