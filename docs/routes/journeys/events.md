# events

This section is dedicated to describing how events are serialized over the
various websocket routes. They are sent on a best-effort basis to the client
to give more accurate journey times than solely easing the totals would give,
but the client can't rely on them being sent.

They have the following form:

```json
{
    "uid": "string",
    "user_sub": "string",
    "session_uid": "string",
    "evtype": "string",
    "journey_time": 0,
    "data": {
        "key": "value"
    }
}
```

where the `user_sub` and `session_uid` may be omitted by the server

The `data` field on each event depends on the event type. The event types,
and respective data fields are:

-   `join`: A user joined the journey. The data field is as follows:

    ```json
    {}
    ```

-   `leave`: A user left the journey. The data field is as follows:

    ```json
    {}
    ```

-   `like`: A user liked the journey. The data field is as follows:

    ```json
    {}
    ```

-   `numeric_prompt_response`: Used when the journey has a numeric prompt and a
    user provided a response. The journey prompt type and options can be found
    from the HTTP api. The data field is as follows:

    ```json
    {
        "rating": 1
    }
    ```

    where `rating` is the numeric response given by the user

-   `press_prompt_start_response`: Used when the journey has a press prompt
    and a user started pressing the button. The data field is as follows:

    ```json
    {}
    ```

-   `press_prompt_end_response`: Used when the journey has a press prompt
    and a user stopped pressing the button. The data field is as follows:

    ```json
    {}
    ```

-   `color_prompt_response`: Used when the journey has a color prompt and a
    user provided a response. The journey prompt type and options can be found
    from the HTTP api. The data field is as follows:

    ```json
    {
        "index": 0
    }
    ```

    where `index` refers to one of the colors in the journey prompt options

-   `word_prompt_response`: Used when the journey has a word prompt and a user
    provided a response. The journey prompt type and options can be found from
    the HTTP api. The data field is as follows:

    ```json
    {
        "index": 0
    }
    ```

    where `index` refers to one of the words in the journey prompt options
