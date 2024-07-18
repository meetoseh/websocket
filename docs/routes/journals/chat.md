# /api/2/journals/chat

Used for streaming a chat response progress to a user. This
is mostly a thin, authenticated wrapper around a redis pubsub
channel.

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

This is a four-part handshake:

- `AuthRequest`: the client provides the jwt to the server to prove it can
  access the chat item
- `AuthResponse`: the server acknowledges the auth response and moves onto
  streaming events.

### AuthRequest

The client must send the following packet immediately after connecting:

```json
{
  "type": "authorize",
  "data": {
    "jwt": "string"
  }
}
```

Where:

The JWTs for this endpoint have the following claims:

- is signed using `RS256` using the `OSEH_JOURNAL_JWT_SECRET`
- the `sub` is the `uid` of the journal entry
- the `oseh:journal_entry_item_uid` is the uid of the journal entry item that
  is being generated
- the `oseh:journal_client_key_uid` is the uid of the journal client key that
  is being used as an additional layer of encryption for the actual text of
  the journal entry item
- the `oseh:user_sub` is the sub of the user who owns the journal entry item,
  as an additional sanity check before decrypting
- the `aud` is `oseh-journal-chat`
- the `iss` is `oseh`
- must have `exp` and `iat`

#### Errors

The code, type pairs are as follows:

- `422`, `unprocessable_entity`: the auth request packet was malformed, such
  as a missing field.
- `403`, `forbidden`: the JWT was invalid or expired
- `404`, `not_found`: the journal entry item is not available and not expected
  to become available, such as if the journal entry it is a part of has been
  deleted or the journal client key could not be found. This should not be used
  to delete the journal client key; use rest endpoints for that and treat mismatches
  as server errors.

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

where the events are a list of events which are described in detail [here](events.md)
