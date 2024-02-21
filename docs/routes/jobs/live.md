# /api/2/jobs/live

This endpoint streams progress events to the client.

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

The initial handshake is responsible for authorizing the client.

- `AuthRequest`: the client provides the jwt to the server to prove it can
  access the interactive prompt events
- `AuthResponse`: the server acknowledges the auth request and moves onto
  streaming events.

### AuthRequest

The client must send the following packet immediately after connecting:

```json
{
  "type": "authorize",
  "data": {
    "job_uid": "string",
    "jwt": "string"
  }
}
```

Where:

- The `job_uid` is the uid of the job whose progress is to be watched

The JWTs for this endpoint have the following claims:

- is signed using `RS256` using the `OSEH_PROGRESS_JWT_SECRET`
- the `sub` is the `uid` of the job progress, and thus must match the provided `job_uid`
- the `aud` is `oseh-job-progress`
- the `iss` is `oseh`
- must have `exp` and `iat`

#### Errors

The code, type pairs are as follows:

- `422`, `unprocessable_entity`: the auth request packet was malformed, such
  as a missing field.
- `403`, `forbidden`: the JWT was invalid, expired, or not for the given
  interactive prompt.
- `404`, `not_found`: the job progress event stream is not available, probably
  due to an error or because 30m have passed since the last event

### AuthResponse

The server will reply to the AuthRequest with a packet in the following form:

```json
{
  "success": true,
  "type": "auth_response",
  "uid": "string",
  "data": {}
}
```

## Events

The server will periodically send events in the form of an EventBatch packet

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

where the events are a list of events in the following form:

```json
{
  "type": "queued",
  "message": "waiting for a worker to be available",
  "indicator": { "type": "spinner" },
  "occurred_at": 0
}
```

where

- `type (string enum)`: basic enum for the category of event
  - `queued`: typically the first event pushed, usually at the same time or just prior to
    queueing the job in `jobs:hot`
  - `started`: pushed when the jobs runner picks up the job
  - `bounce`: pushed when the jobs runner is going to bounce the job back to the queue,
    usually due to receiving a term signal
  - `spawned`: pushed when the jobs runner created a new, independent job uid that is related
  - `progress`: most common event; used to update the message/indicator because we made progress
  - `failed`: terminal event, indicates the job failed
  - `succeeded`: terminal event, indicates the job succeeded
- `message (string)`: freeform text, usually less than 255 characters, meant to be shown
  to the user
- `spawned (object, null)`: specified iff type is `spawned`, information about the spawned job
  - `uid (string)`: the uid of the spawned job; this is a job progress uid
  - `jwt (string)`: a jwt that can be used to access the spawned job's progress
  - `name (string)`: a hint for the name of this job for the client
- `indicator (object, null)`: hint for how this step can be visually communicated. `null`
  means no indicator, just the message. Each form the object can take has a
  distinct `type (string, enum)`:
  - `bar`: indicates a progress bar would be appropriate
    - `at (number)`: how many steps are finished
    - `of (number)`: total number of steps
  - `spinner`: indicates a spinner would be appropriate
  - `final`: indicates that the user shouldn't expect more messages
- `occurred_at (float)`: when this event occurred in seconds since the epoch. note that
  this timestamp may be sent from different servers and thus may be effected by clock
  drift. the returned order is the real order the events occurred in, even if the timestamps
  are ordered differently.
