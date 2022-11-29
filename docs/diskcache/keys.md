# diskcache

the keys we store locally on websocket instances via diskcache.

-   `journeys:{uid}:meta`: meta information about the journey with the given uid.
    [used here](../../journeys/lib/meta.py)

    ```json
    {
        "uid": "string",
        "duration_seconds": 0
    }
    ```

    where `uid` is just the uid of the journey, `duration_seconds` is the duration
    of the journeys audio content
