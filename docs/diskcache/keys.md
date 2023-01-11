# diskcache

the keys we store locally on websocket instances via diskcache.

- `journeys:{uid}:meta`: meta information about the journey with the given uid.
  [used here](../../journeys/lib/meta.py)

  ```json
  {
    "uid": "string",
    "duration_seconds": 0
  }
  ```

  where `uid` is just the uid of the journey, `duration_seconds` is the duration
  of the journeys audio content

- `updater-lock-key` goes to a random token for the token we used to acquire
  the updater lock before shutting down to update. See the redis key
  `updates:{repo}:lock` for more information.
