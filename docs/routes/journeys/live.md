# /api/2/journeys/{uid}/live

This endpoint streams live events for a journey to the client. It allows
for a configurable bandwidth limit which is respected using a token bucket
algorithm.

## Errors

For any client-sent packet the server may respond with an error packet. The
error packets have the following form:

```json
{
    "success": false,
    "type": "error",
    "uid": "string",
    "data": {
        "code": 400,
        "type": "string",
        "message": "string"
    }
}
```

where the code is interpreted as a HTTP status code and the type is a string
enum which depends on the request. The message is a human-readable string
describing the error.

## Initial Handshake

The initial handshake is responsible for authorizing the client, syncing
a journey time clock with the server, and selecting the bandwidth. the client
should reconnect with a different bandwidth when performing client-side
adaptive bandwidth.

This is a four-part handshake:

-   `AuthRequest`: the client provides the jwt to the server to prove it can
    access the journey events
-   `SyncRequest`: the server asks the client to echo back the journey time.
    this is typically a negative number to allow time for buffering. The
    server keeps track of the originate time of this request.
-   `SyncResponse`: the client echoes back the journey time when it received
    the sync request and the journey time when it sent the sync response.
-   `AuthResponse`: the server acknowledges the sync response and moves onto
    streaming events.

### AuthRequest

The client must send the following packet immediately after connecting:

```json
{
    "type": "authorize",
    "data": {
        "journey_uid": "string",
        "jwt": "string",
        "bandwidth": 100,
        "lookback": 4,
        "lookahead": 4
    }
}
```

Where:

-   The `bandwidth` is the maximum number of events per second the client would
    like to receive.
-   The `lookback` is the maximum number of seconds prior to the clients journey
    time that the client would like to receive events for. For example, if the lokback
    is 4, then when the client reaches journey time 10, the server will stream new events
    which occurred at a journey time no less than 6.
-   The `lookahead` is the maximum number of seconds after the clients journey time
    that the client would like to receive events for. For example, if the lookahead
    is 4, then when the client reaches journey time 10, the server will stream new events
    which occurred at a journey time no greater than 14.

The JWTs for this endpoint have the following claims:

-   is signed using `RS256` using the `OSEH_JOURNEY_JWT_SECRET`
-   the `sub` is the `uid` of the `journey`, and thus must match the provided `journey_uid`
-   the `aud` is `oseh-journey`
-   the `iss` is `oseh`
-   must have `exp` and `iat`

#### Errors

The code, type pairs are as follows:

-   `422`, `unprocessable_entity`: the auth request packet was malformed, such
    as a missing field.
-   `403`, `forbidden`: the JWT was invalid, expired, or not for the given
    journey.
-   `404`, `not_found`: the journey event stream is not available, such as
    because the journey has been deleted or hasn't started yet.

### SyncRequest

The server will reply to the AuthRequest with a packet in the following form:

```json
{
    "success": true,
    "type": "sync_request",
    "uid": "string",
    "data": {}
}
```

### SyncResponse

The client must reply to the SyncRequest with a packet in the following form:

```json
{
    "type": "sync_response",
    "data": {
        "receive_timestamp": 0,
        "transmit_timestamp": 0
    }
}
```

The `receive_timestamp` is the journey time when the client received the
sync request and the `transmit_timestamp` is the journey time when the client
sent the sync response. They are typically close to each other, and they may
be negative if the client is allowing time for buffering. The transmit time
must be at least the receive time.

#### Errors

The code, type pairs are as follows:

-   `422`, `unprocessable_entity`: the packet was malformed, such as a missing
    field.

### AuthResponse

The server will reply to the SyncResponse with a packet in the following form:

```json
{
    "success": true,
    "type": "auth_response",
    "uid": "string",
    "data": {}
}
```

## Events

The server will periodically send events in the form of an EventBatch packet and
latency detection packets in the form of a LatencyDetection packet. If the
client sends any packet during this phase they will either be ignored or
disconnected.

### EventBatch

The server will send batches of events with the following packet:

```json
{
    "success": true,
    "type": "event_batch",
    "data": {
        "events": []
    }
}
```

where the events are a list of events which are described in detail [here](events.md)

### LatencyDetection

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

The `expected_receive_journey_time` is what the server thinks that the clients
journey time will be at the moment it receives the packet. If this differs
significantly from the clients journey time, the client should reconnect,
potentially with a new bandwidth.
