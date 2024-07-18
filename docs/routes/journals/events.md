# events

This section is dedicated to describing how events are serialized for the chat
endpoint.

A journal chat describes a contiguous subset of journal entry items within a
single journal entry, but the number of those items is not necessarily known
when the chat is started.

These events are going to describe mutations to an object which starts like this:

```json
{
  "uid": "oseh_jc_placeholder",
  "integrity": "string",
  "data": []
}
```

where `integrity` is the hex sha256 value of the JSON stringified with sorted keys
and simple spacing `data` field (and should be rechecked by the client), and
`data` is an array that is filled to look like:

```json
[
  {
    "uid": "string",
    "integrity": "string",
    "data": {
      "type": "chat",
      "data": {
        "type": "textual",
        "parts": [
          { "type": "paragraph", "value": "some text" },
          { "type": "journey", "uid": "oseh_j_placeholder" }
        ]
      }
    }
  }
]
```

The data is a `JournalEntryItemData`, i.e., something that would be stored in
a journal entry item records `data` column.

Events have the following form:

```json
{
  "uid": "string",
  "data": {
    "type": "string"
  }
}
```

where `uid` is an identifier for the event in case the client later wants to
report an error, and `data` is an object which always has a string-valued "type"
key, which determines the rest of the structure of the data.

## Event Types

The event types, and respective data fields are:

### thinking-bar

Indicates that the server is working on the request and acts as a hint that
the client should display a loading indicator in the form of a progress bar

```json
{
  "uid": "string",
  "data": {
    "type": "thinking-bar",
    "at": 3,
    "of": 10,
    "message": "short text",
    "detail": "optional longer text"
  }
}
```

### thinking-spinner

Indicates that the server is working on the request and acts as a hint that
the client should display a loading indicator in the form of a spinner

```json
{
  "uid": "string",
  "data": {
    "type": "thinking-spinner",
    "message": "short text",
    "detail": "optional longer text"
  }
}
```

### error

Indicates that an error occurred while processing the request

```json
{
  "uid": "string",
  "data": {
    "type": "error",
    "code": 400,
    "message": "short text",
    "detail": "optional longer text"
  }
}
```

The code should be interpeted like an HTTP status code, and the message is like
an HTTP status message, with detail acting like the HTTP text body. After this
is sent, the client will have a few seconds to disconnect before the server will
close the connection.

### chat

Indicates that part of the message is available.

```json
{
  "uid": "string",
  "data": {
    "type": "chat",
    "encrypted_segment_data": "string",
    "more": true
  }
}
```

Where the segment data is a JSON object, stringified, encrypted with the journal
client key (Fernet symmetric encryption), then urlsafe base64 encoded. It
contains a JSON object with the following structure

```json
{
  "mutations": [
    {
      "key": ["integrity"],
      "value": "string"
    },
    {
      "key": ["data", 0],
      "value": {}
    }
  ]
}
```

where each mutation specifies a path to where the value should be inserted or
updated named `key`, and the value to insert or update named `value`. Integer
parts of the key are for arrays; if the array is shorter than the required length,
it must be padded with `null`. After applying all the mutations in the event,
the client MUST verify that all integrity checks pass, which ensures it parsed
the event correctly.

Note that the server MAY write or edit parts in any order, so it is not necessarily
an error if it is not strictly appending to paragraphs (for example).

`more` is true if there will be more parts to the message, and false if this is the
last part. After `more` with `false`, the client will only have a few seconds to
disconnect before the server will close the connection.
