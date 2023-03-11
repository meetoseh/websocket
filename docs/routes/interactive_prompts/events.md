# events

This section is dedicated to describing how events are serialized over the
various websocket routes. They are sent on a best-effort basis to the client
to give more accurate prompt times than solely easing the totals would give,
but the client can't rely on them being sent.

They have the following form:

```json
{
  "uid": "string",
  "user_sub": "string",
  "session_uid": "string",
  "evtype": "string",
  "prompt_time": 0,
  "icon": {
    "uid": "string",
    "jwt": "string"
  },
  "data": {
    "key": "value"
  }
}
```

where the `user_sub`, `session_uid`, and `icon` may be omitted by the server

The `data` field on each event depends on the event type. The event types,
and respective data fields are:

- `join`: A user joined the interactive prompt. The data field is as follows:

  ```json
  { "name": "string" }
  ```

- `leave`: A user left the interactive prompt. The data field is as follows:

  ```json
  { "name": "string" }
  ```

- `like`: A user liked the interactive prompt. The data field is as follows:

  ```json
  {}
  ```

- `numeric_prompt_response`: Used when the interactive prompt has a numeric prompt and a
  user provided a response. The interactive prompt type and options can be found
  from the HTTP api. The data field is as follows:

  ```json
  {
    "rating": 1
  }
  ```

  where `rating` is the numeric response given by the user

- `press_prompt_start_response`: Used when the interactive prompt has a press prompt
  and a user started pressing the button. The data field is as follows:

  ```json
  {}
  ```

- `press_prompt_end_response`: Used when the interactive prompt has a press prompt
  and a user stopped pressing the button. The data field is as follows:

  ```json
  {}
  ```

- `color_prompt_response`: Used when the interactive prompt has a color prompt and a
  user provided a response. The interactive prompt type and options can be found
  from the HTTP api. The data field is as follows:

  ```json
  {
    "index": 0
  }
  ```

  where `index` refers to one of the colors in the interactive prompt options

- `word_prompt_response`: Used when the interactive prompt has a word prompt and a user
  provided a response. The interactive prompt type and options can be found from
  the HTTP api. The data field is as follows:

  ```json
  {
    "index": 0
  }
  ```

  where `index` refers to one of the words in the interactive prompt options
