# /api/2/journeys/{uid}

Subscribe to the (re)broadcasted events for the given journey. Starts with an
authorization handshake followed by events being streamed from the server to
the client, with occasional time syncing.

The client can post new journey events using the HTTP endpoints, e.g.,
`POST /api/1/journeys/events/like`. Once they are processed, they will be
broadcasted back to the client via this websocket, though the client should
provide more immediate feedback to the user - potentially even in anticipation
of a successful http response - and disregard the duplicate when it comes through
the websocket.

## Initial Handshake

### Authorization

The client must send a packet, referred to as the AuthRequestPacket, in the
following form immediately after starting the connection:

```json
{
    "type": "authorize",
    "data": {
        "journey_uid": "string",
        "jwt": "string"
    }
}
```

The server will respond either with of the following, referred to as the
AuthResponsePacket or ErrorPacket, respectively:

```json
{
    "success": true,
    "type": "auth_response"
}
```

```json
{
    "success": false,
    "type": "error",
    "data": {
        "code": 400,
        "type": "string",
        "message": "string"
    }
}
```

where the code, type pairs are as follows:

-   `422`, `unprocessable_entity`: the auth request packet was malformed, such
    as a missing field.
-   `403`, `forbidden`: the JWT was invalid, expired, or not for the given
    journey.
-   `404`, `not_found`: the journey event stream is not available, such as
    because the journey has been deleted or hasn't started yet.

The JWTs for this endpoint have the following claims:

-   is signed using `RS256` using the `OSEH_JOURNEY_JWT_SECRET`
-   the `sub` is the `uid` of the `journey`, and thus must match the provided `journey_uid`
-   the `aud` is `oseh-journey`
-   the `iss` is `oseh`
-   must have `exp` and `iat`

Note how this endpoint does not receive the user sub, directly or indirectly, as it's
not needed for a one-way stream of events.

## Event Stream

### Time State

The time state refers to the journey time that the server is broadcasting events at, and
the rate at which it is broadcasting them. The client can perform a Time Sync handshake
to adjust the time state. The client will generally need to perform this handshake immediately
after authorization as the time state starts paused.

The time state is initially as follows:

```json
{
    "journey_time": 0,
    "buffer_max_time": 2,
    "buffered_to_journey_time": 0,
    "buffered_to_journey_uid": null,
    "rate": 0,
    "bandwidth_events": 100,
    "bandwidth_strategy": "random",
    "bandwidth_window": 1.0
}
```

Which means that the server is at the beginning of the journey (`journey_time = 0`), it wants
to send events up to 2 seconds (`journey_time + buffer_max_time`) so the client has them in
advance of needing them, it's advancing time at 0 seconds/second (`rate = 0`), and it's going
to restrict the event stream if it exceeds 100 events/second (`bandwidth_events = 100`). When
restricting events, it will randomly select events to drop with a probability that is proportional
to how many events need to be dropped in the interval (`bandwidth_strategy = "random"`). Events
will be dropped if the bandwidth is exceeded over a rolling window of 1 second (`bandwidth_window = 1.0`).

Fields:

-   `journey_time`: the clients current journey time as predicted by the server.
    Note that this is a computed field rather than a direct field, however, it
    is practical to think of it as if it were a field. It is computed from an
    offset to `time.perf_counter()` on the server in the primary process and
    thread handling the websocket.
-   `buffer_max_time`: the server will attempt to send known events up to this
    many seconds in the future. this means that events may arrive out of order,
    for example, when the server is buffered 1 second into the future but a live
    event is received near the clients journey time.
-   `buffered_to_journey_time`: the server has sent known events up to this far
    in the future already. `journey_time + buffer_max_time >= buffered_to_journey_time`
-   `buffered_to_journey_uid`: since there can be multiple events at a particular
    journey time, just the `buffered_to_journey_time` is not sufficient for server-side
    pagination. Hence, the server will also store the `uid` of the last buffered
    event sent.
-   `rate`: the rate at which the server is advancing the journey time. this is
    the number of seconds per second that the server is advancing the journey time.
    only non-negative values are allowed; to rewind time, the client can perform
    a time sync with a journey time in the past
-   `bandwidth_events`: the maximum number of events that can be sent per second
    over the window. A higher value provides a higher fidelity experience, but only
    if the client can actually receive and process all the events, hence the client
    should select the highest value it can consistently handle. The algorithm for
    this is described under Client Adaptive Bandwidth. Minimum value is 10, maximum
    value is 5,000.
-   `bandwidth_strategy`: the strategy for dropping events when the bandwidth is
    exceeded. currently, only "random" is supported, which drops events randomly
    with a probability that is proportional to how many events need to be dropped
    in the interval.
-   `bandwidth_window`: the rolling window over which the bandwidth is measured.
    a higher value allows for spikier events. For example, for a fixed `bandwidth_events`
    of 100, a `bandwidth_window` of 1 means no more than 100 events in 1 second,
    whereas a `bandwidth_window` of 2 means no more than 200 events in 2 seconds.
    So if there are 200 events at `t=5` and then no events until `t=7`, the first
    will result in throttling and the second will not. Generally a higher
    bandwidth window results in a more consistent experience at the cost of more
    memory usage. Minimum value is 0.25, maximum value is 8.0.

### Time Sync

For the purpose of this section, "oseh server" is the websocket server that
recieved the initial AuthRequestPacket, and "oseh client" is the websocket
client that sent the AuthRequestPacket.

At any point the oseh client may request a time sync, which will ensure the oseh
server and oseh client agree on the current time state. This is done in a manner
reminiscent to unicast SNTP (RFC 1769, Simple Network Time Protocol), where the
oseh client is acting as the SNTP server.

This process has four parts, briefly described as :

-   InitiateSNTPPacket: oseh client updates settings and requests the oseh server sync
    its journey time
-   SNTPRequestPacket: oseh server provides timing information to the oseh client to
    sync its clock
-   SNTPResponsePacket: oseh client responds to the oseh server with the necessary timing
    information to perform a latency-corrected time sync
-   TimeSyncConfirmationPacket: oseh server acknowledges that the time was synced

#### Initial Packet (InitiateSNTPPacket)

Sent by the oseh client to the oseh server. Note that the journey time is not specified
by the oseh client by this time, as that will be handled in the SNTP portion. The client
can adjust rate and bandwidth settings during this packet, which will only take effect
when the time sync completes successfully.

The journey is effectively playing when the rate is 1, and paused when the rate is 0.
Other rates are not supported at this time.

```json
{
    "type": "initiate_sntp",
    "uid": "string",
    "data": {
        "rate": 1,
        "bandwidth_events": 5000,
        "bandwidth_strategy": "random",
        "bandwidth_window": 1.0
    }
}
```

If the oseh server rejects the packet, it will do so with the following
shape:

```json
{
    "success": false,
    "type": "error",
    "uid": "string | null",
    "data": {
        "code": 422,
        "type": "unprocessable_entity",
        "message": "string"
    }
}
```

With the only `code`, `type` pair being `422`, `unprocessable_entity` to
indicate that the time sync packet was malformed. The uid will be echo'd
back, if provided, to help the client match up requests and responses.

#### SNTP Request Packet (SNTPRequestPacket)

Sent by the oseh server to the oseh client, this is somewhat analagous to the
first step in the SNTP (RFC 1769) protocol to sync clock time. This step will
allow the oseh server to deduce the round trip time and local clock offset of
the oseh client compared to the oseh server, which can then be used to treat the
journey time as an offset from the oseh clients local clock, which the oseh
server can find as an offset from the oseh servers local clock.

The oseh server packet will be in the following form:

```json
{
    "success": true,
    "type": "sntp_request",
    "uid": "string"
}
```

On the oseh server it will store the `originate_timestamp` associated with the
uid with `time.perf_counter()`. The client does not need this information and
hence it's not transmitted.

#### SNTP Response Packet (SNTPResponsePacket)

When the oseh client receives the SNTPRequestPacket, it will respond with the
SNTP server information required for the oseh server to determine the appropriate
offset to match the oseh client.

The oseh client packet should be in the following format:

```json
{
    "type": "sntp_response",
    "uid": "string",
    "data": {
        "receive_timestamp": 0,
        "transmit_timestamp": 0
    }
}
```

Where the `receive_timestamp` and `transmit_timestamp` are `journey_time` values
at the time the `sntp_request` was received and when the `sntp_response` was sent,
respectively. Note that if `rate=0`, then both timestamps should be identical, as
the journey time is not advancing.

When the server receives the SNTPResponsePacket, it will have four relevant
timestamps:

-   `originate_timestamp`: the time the `sntp_request` was sent by the oseh server
-   `receive_timestamp`: the time the `sntp_request` was received by the oseh client
-   `transmit_timestamp`: the time the `sntp_response` was sent by the oseh client
-   `destination_timestamp`: the time the `sntp_response` was received by the oseh server

Which are used to deduce:

```py
# NOTE: RFC1769 incorrectly calculates the roundtrip delay (wrong sign on the second term)
# it listed correctly here. see also https://www.eecis.udel.edu/~mills/time.html for the
# correct formula and explanation
round_trip_delay = (destination_timestamp - originate_timestamp) - (transmit_timestamp - receive_timestamp)
local_clock_offset = ((receive_timestamp - originate_timestamp) + (transmit_timestamp - destination_timestamp)) / 2
```

From there, the oseh server `journey_time` is calculated as:

```py
journey_time = local_time + local_clock_offset + (round_trip_delay / 2)
```

Note that this calculation assumes that the latency from the oseh server to the oseh client
is equal to the latency from the oseh client to the oseh server. If this does not hold,
the synchronization has a systematic bias of half the difference between the forward and
backward latency. This is consistent with NTP (see e.g. [here](https://stackoverflow.com/a/18779822)).
It is not possible to measure one-way latency in general, which is why, for example, we
can't measure the one-way speed of light! See e.g. [here](https://en.wikipedia.org/wiki/One-way_speed_of_light)
for an explanation.

Upon receiving this packet the oseh server will respond with the following if the
packet was rejected:

```json
{
    "success": false,
    "type": "error",
    "uid": "string | null",
    "data": {
        "code": 422,
        "type": "unprocessable_entity",
        "message": "string"
    }
}
```

where the only `code`, `type` pair being `422`, `unprocessable_entity` to
indicate that the sntp response packet was malformed.

Otherwise, it will respond with a TimeSyncConfirmationPacket.

#### Time Sync Confirmation Packet (TimeSyncConfirmationPacket)

This packet serves no purpose except for consistency in having the oseh
server always acknowledge the oseh clients packets (i.e., it gets the
final word).

```json
{
    "success": true,
    "type": "time_sync_confirmation",
    "uid": "string"
}
```

### Event Batch

An event batch is a list of events streamed from the server to the client. The
list may be out of order, as it can mix live events with events from the past -
where generally live events will have an earlier journey time than past events
(since past events can be buffered in advance)

The format of an event batch is:

```json
{
    "success": true,
    "type": "event_batch",
    "data": {
        "events": [
            {
                "uid": "string",
                "user_sub": "string",
                "type": "string",
                "journey_time": 0,
                "data": {
                    "key": "value"
                }
            }
        ]
    }
}
```

where the `data` field on each event depends on the event type. The event types,
and respective data fields are:

-   `join`: A user joined the journey. The data field is as follows:

    ```json
    {
        "total": 0
    }
    ```

    where `total` is the total number of people in the journey at the time of
    the event, in case some packets were dropped.

-   `leave`: A user left the journey. The data field is as follows:

    ```json
    {
        "total": 0
    }
    ```

    where `total` is the total number of people in the journey at the time of
    the event, in case some packets were dropped.

-   `like`: A user liked the journey. The data field is as follows:

    ```json
    {
        "total": 0
    }
    ```

    where total is the new total number of likes after this event, in case
    some packets were dropped.

-   `numeric_prompt_response`: Used when the journey has a numeric prompt and a
    user provided a response. The journey prompt type and options can be found
    from the HTTP api. The data field is as follows:

    ```json
    {
        "rating": 1,
        "counts_by_rating": [1, 0, 0, 0, 0]
    }
    ```

    where `rating` is the numeric response given by the user, and `counts` is
    the new counts of responses for each rating, in case some packets were dropped.

-   `press_prompt_start_response`: Used when the journey has a press prompt
    and a user started pressing the button. The data field is as follows:

    ```json
    {
        "current": 0,
        "total": 0
    }
    ```

    where `current` is the number of people pressing the button right now and
    `total` is the total number of times the button has been pressed, in case
    some packets were dropped.

-   `press_prompt_end_response`: Used when the journey has a press prompt
    and a user stopped pressing the button. The data field is as follows:

    ```json
    {
        "current": 0,
        "total": 0
    }
    ```

    where `current` is the number of people pressing the button right now and
    `total` is the total number of times the button has been pressed, in case
    some packets were dropped.

-   `color_prompt_response`: Used when the journey has a color prompt and a
    user provided a response. The journey prompt type and options can be found
    from the HTTP api. The data field is as follows:

    ```json
    {
        "index": 0,
        "counts_by_index": [1, 0, 0, 0, 0]
    }
    ```

    where `index` refers to one of the colors in the journey prompt options,
    and `counts` is the new counts of responses for each color, in case some
    packets were dropped.

-   `word_prompt_response`: Used when the journey has a word prompt and a user
    provided a response. The journey prompt type and options can be found from
    the HTTP api. The data field is as follows:

    ```json
    {
        "index": 0,
        "counts_by_index": [1, 0, 0, 0, 0]
    }
    ```

    where `index` refers to one of the words in the journey prompt options,
    and `counts` is the new counts of responses for each word, in case some
    packets were dropped.

### Latency Detection

The server will regularly send latency detection packets to assist the client
with adaptive bandwidth. These packets will have the following form:

```json
{
    "success": true,
    "type": "latency_detection",
    "data": {
        "expected_receive_journey_time": 0.0
    }
}
```

`expected_receive_journey_time` indicates what the clients journey time should
be at the moment it receives this packet, accounting for the round trip delay
from the last time sync. Specifically, this is the server-side journey time at
the time the batch was sent. See Client Adaptive Bandwidth for how this is
converted into new time sync events.

This is sent as its own packet to ensure theres minimal computational delays - if
it were sent in the event batch, the time required to serialize the event batch
could cause a significant systematic bias for the client to think it's more
lagged than it actually is.

## Client Adaptive Bandwidth

This section describes how the client should select `bandwidth_events` based on
the available processing power and network conditions. This is the only variable
that should be adapted by the client.

Arbitrary clients cannot be guarranteed to follow this algorithm, however not
doing so will only degrade performance. TCP congestion control, which
inspired this algorithm, as specified in
[RFC 2581](https://datatracker.ietf.org/doc/html/rfc2581) is also susceptible to
this attack despite it being possible to
[mitigate](https://cseweb.ucsd.edu/~savage/papers/CCR99.pdf), which suggests
this type of attack is not popular - probably because it degrades into a
non-multiplicative, protocol-specific, denial of service attack - which is not
generally viable. Nonetheless, this attack is mitigated somewhat by the limits
placed on `bandwidth_events`.

Websockets are transmitted over TCP, so they will arrive in order. Packet loss
will thus appear as latency. While not receiving packets the client cannot
distinguish between latency and a simple lack of events over the window. Thus
the latency detection packets are required to detect packet loss or other forms
of network congestion.

The bandwidth events will use the additive-increase/multiplicative-decrease
([AIMD](https://en.wikipedia.org/wiki/Additive_increase/multiplicative_decrease))
algorithm, where the increase is 500 and the decrease is 0.5, with a minimum
bandwidth of 10 events/second and a maximum bandwidth of 5,000 events/second.
The minimum can be reached in 4 seconds, the maximum can be reached in 10
seconds. Thus it's reasonable to say this algorithm has a 10 second warmup
period.

The AIMD algorithm is applied to the bandwidth events pseudo-independently for
network conditions and processing conditions, and the applied value is always
the lower of the two.

The client will have the following state for adaptive bandwidth, shown with
their initial values

```json
{
    "bandwidth_events": 100,
    "target_bandwidth_events": {
        "cpu": 100,
        "network": 100
    }
}
```

The client should do a time sync event to have the bandwidth events set to the
target bandwidth events (the lesser of the cpu and network), but not more often
than once per second.

As it receives events in batches, it should be pushed onto a min-heap based on
the journey time of the events.

During the update loop, the client should process events from the heap until the
top of the heap has a journey time greater than the current journey time. The
client should be mindful of the target duration of the update loop as it's
typically on the main thread. The client should switch to simply dropping
packets when near the target duration. If it does this, the target bandwidth
events for `cpu` should be set to half the current bandwidth events, but not
less than 10. If it does not do this for a period of at least 1 second, the
target bandwidth events for `cpu` should be set to the current bandwidth events
plus 500, but not more than 5,000.

When the client receives a latency detection packet, if it is more than 0.5
seconds before the target time, it should set the target bandwidth events for
`network` to half the current bandwidth events, but not less than 10

If it is at least 0.5s after the current journey time, it should set the target
bandwidth events for `network` to the current bandwidth events plus 500, but not
more than 5,000.
