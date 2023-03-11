# diskcache

the keys we store locally on websocket instances via diskcache.

- `interactive_prompts:{uid}:meta`: meta information about the interactive prompt with the given uid.
  [used here](../../interactive_prompts/lib/meta.py)

  ```json
  {
    "uid": "string",
    "duration_seconds": 0
  }
  ```

  where `uid` is just the uid of the interactive prompt and `duration_seconds` is
  how long the interactive prompt's interactive portion is

- `updater-lock-key` goes to a random token for the token we used to acquire
  the updater lock before shutting down to update. See the redis key
  `updates:{repo}:lock` for more information.
