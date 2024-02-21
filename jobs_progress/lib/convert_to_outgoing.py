from itgs import Itgs
from jobs_progress.auth import create_jwt
from jobs_progress.lib.packets import (
    JobProgressIncomingModel,
    JobProgressOutgoingModel,
    JobProgressSpawnedInfoOutgoingModel,
    JobProgressSpawnedOutgoingModel,
)


async def convert_to_outgoing(
    itgs: Itgs, inc: JobProgressIncomingModel
) -> JobProgressOutgoingModel:
    """Converts the internal representation of the given progress update to
    the external representation.
    """
    if inc.type != "spawned":
        return inc

    return JobProgressSpawnedOutgoingModel(
        type="spawned",
        message=inc.message,
        indicator=inc.indicator,
        spawned=JobProgressSpawnedInfoOutgoingModel(
            uid=inc.spawned.uid,
            name=inc.spawned.name,
            jwt=await create_jwt(itgs, inc.spawned.uid),
        ),
        occurred_at=inc.occurred_at,
    )
