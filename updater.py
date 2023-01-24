"""Handles updating when the repository is updated"""
from itgs import Itgs
import perpetual_pub_sub as pps
import asyncio
import subprocess
import platform
import secrets
from loguru import logger
import socket
import os


async def _listen_forever():
    """Subscribes to the redis channel updates:websocket and upon
    recieving a message, calls /home/ec2-user/update_webapp.sh
    """
    async with Itgs() as itgs:
        await release_update_lock_if_held(itgs)

        if os.environ.get("ENVIRONMENT") != "dev":
            slack = await itgs.slack()
            await slack.send_ops_message(f"websocket {socket.gethostname()} ready")

    async with pps.PPSSubscription(pps.instance, "updates:websocket", "updater") as sub:
        await sub.read()

    async with Itgs() as itgs:
        await acquire_update_lock(itgs)

    do_update()


async def acquire_update_lock(itgs: Itgs):
    our_identifier = secrets.token_urlsafe(16).encode("utf-8")
    local_cache = await itgs.local_cache()

    redis = await itgs.redis()
    while True:
        local_cache.set(b"updater-lock-key", our_identifier, expire=310)
        success = await redis.set(
            b"updates:websocket:lock", our_identifier, nx=True, ex=300
        )
        if success:
            break
        await asyncio.sleep(1)


DELETE_IF_MATCH_SCRIPT = """
local key = KEYS[1]
local expected = ARGV[1]

local current = redis.call("GET", key)
if current == expected then
    redis.call("DEL", key)
    return 1
end
return 0
"""


async def release_update_lock_if_held(itgs: Itgs):
    local_cache = await itgs.local_cache()

    our_identifier = local_cache.get(b"updater-lock-key")
    if our_identifier is None:
        return

    redis = await itgs.redis()
    await redis.eval(
        DELETE_IF_MATCH_SCRIPT, 1, b"updates:websocket:lock", our_identifier
    )
    local_cache.delete(b"updater-lock-key")


def do_update():
    if platform.platform().lower().startswith("linux"):
        subprocess.Popen(
            "bash /home/ec2-user/update_webapp.sh > /dev/null 2>&1",
            shell=True,
            stdin=None,
            stdout=None,
            stderr=None,
            preexec_fn=os.setpgrp,
        )
    else:
        subprocess.Popen(
            "bash /home/ec2-user/update_webapp.sh",
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
            close_fds=True,
        )


async def listen_forever():
    """Subscribes to the redis channel updates:websocket and upon
    recieving a message, calls /home/ec2-user/update_webapp.sh
    """
    if os.path.exists("updater.lock"):
        logger.warning("updater already running; updater loop exiting")
        return

    with open("updater.lock", "w") as f:
        f.write(str(os.getpid()))

    try:
        await _listen_forever()
    finally:
        os.unlink("updater.lock")
        logger.info("updater shutdown")


def listen_forever_sync():
    """Subscribes to the redis channel updates:websocket and upon
    recieving a message, calls /home/ec2-user/update_webapp.sh
    """
    asyncio.run(listen_forever())


if __name__ == "__main__":
    listen_forever_sync()
