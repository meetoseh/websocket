"""Handles updating when the repository is updated"""
import perpetual_pub_sub as pps
import subprocess
import platform
import os


async def listen_forever():
    """Subscribes to the redis channel updates:websocket and upon
    recieving a message, calls /home/ec2-user/update_webapp.sh
    """
    async with pps.PPSSubscription(pps.instance, "updates:websocket", "updater") as sub:
        async for _ in sub:
            break
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
