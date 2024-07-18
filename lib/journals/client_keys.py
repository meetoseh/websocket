"""Module for working with journal client keys"""

import io
import secrets
from itgs import Itgs
from typing import Literal, Optional, Union, cast
from dataclasses import dataclass
import cryptography.fernet


@dataclass
class GetJournalClientKeyResultSuccess:
    type: Literal["success"]
    """
    - `success`: We found the journal client key and were able to download it
    """
    user_sub: str
    """The sub of the user the key is for"""
    journal_client_key_uid: str
    """The uid of the key returned"""
    platform: str
    """The platform the key was originally generated for; useful as a sanity check"""
    journal_client_key: cryptography.fernet.Fernet
    """The journal client key data, wrapped with the appropriate way to use it"""
    journal_client_key_data: bytes
    """The raw journal client key data"""


@dataclass
class GetJournalClientKeyResultRevoked:
    type: Literal["revoked"]
    """
    - `revoked`: We know about the journal client key but we won't accept it anymore
      as `revoked_at` was set
    """
    user_sub: str
    """The sub of the user whose key was revoked"""
    journal_client_key_uid: str
    """The uid of the key that was revoked"""


@dataclass
class GetJournalClientKeyResultLost:
    type: Literal["lost"]
    """
    - `lost`: We know about the journal client key, and it is not revoked, but there is
      no corresponding s3 file containing the key
    """
    user_sub: str
    """The sub of the user whose key was lost"""
    journal_client_key_uid: str
    """The uid of the key that was lost"""


@dataclass
class GetJournalClientKeyResultS3Error:
    type: Literal["s3_error"]
    """
    - `s3_error`: We know about the journal client key, and it is not revoked, but there was
        an error fetching the key from s3
    """
    user_sub: str
    """The sub of the user whose key we failed to fetch"""
    journal_client_key_uid: str
    """The uid of the key we failed to fetch"""
    exc: Exception
    """The exception that was raised while fetching the key"""


@dataclass
class GetJournalClientKeyResultNotFound:
    type: Literal["not_found"]
    """
    - `not_found`: There is no journal client key with that uid for that user
    """
    user_sub: str
    """The sub of the user whose key was not found"""
    journal_client_key_uid: str
    """The uid of the key that was not found"""


@dataclass
class GetJournalClientKeyResultParseError:
    type: Literal["parse_error"]
    """
    - `parse_error`: There was an error parsing the journal client key
    """
    user_sub: str
    """The sub of the user whose key we failed to parse"""
    journal_client_key_uid: str
    """The uid of the key we failed to parse"""
    exc: Exception
    """The exception that was raised while parsing the key"""


GetJournalClientKeyResult = Union[
    GetJournalClientKeyResultSuccess,
    GetJournalClientKeyResultRevoked,
    GetJournalClientKeyResultLost,
    GetJournalClientKeyResultS3Error,
    GetJournalClientKeyResultNotFound,
    GetJournalClientKeyResultParseError,
]


async def get_journal_client_key(
    itgs: Itgs,
    /,
    *,
    user_sub: str,
    journal_client_key_uid: str,
    read_consistency: Literal["none", "weak", "strong"],
) -> GetJournalClientKeyResult:
    """Fetches the journal client key with the given uid for the user with the
    given sub

    Args:
        itgs (Itgs): the integrations to (re)use
        user_sub (str): the sub of the user to fetch the key for
        journal_client_key_uid (str): the uid of the key to fetch
        read_consistency (Literal["none", "weak", "strong"]): the read consistency
            to use when fetching the key. Recommended to use `none` then retry not
            found with `weak`, unless you know the key was just created, in which
            case use `weak`
    """
    conn = await itgs.conn()
    cursor = conn.cursor(read_consistency)

    response = await cursor.execute(
        """
SELECT
    user_journal_client_keys.platform,
    user_journal_client_keys.revoked_at,
    s3_files.key
FROM users, user_journal_client_keys
LEFT OUTER JOIN s3_files ON s3_files.id = user_journal_client_keys.s3_file_id
WHERE
    users.sub = ?
    AND users.id = user_journal_client_keys.user_id
    AND user_journal_client_keys.uid = ?
        """,
        (user_sub, journal_client_key_uid),
    )

    if not response.results:
        return GetJournalClientKeyResultNotFound(
            type="not_found",
            user_sub=user_sub,
            journal_client_key_uid=journal_client_key_uid,
        )

    platform = cast(str, response.results[0][0])
    revoked_at = cast(Optional[float], response.results[0][1])
    s3_key = cast(Optional[str], response.results[0][2])

    if revoked_at is not None:
        return GetJournalClientKeyResultRevoked(
            type="revoked",
            user_sub=user_sub,
            journal_client_key_uid=journal_client_key_uid,
        )

    if s3_key is None:
        return GetJournalClientKeyResultLost(
            type="lost",
            user_sub=user_sub,
            journal_client_key_uid=journal_client_key_uid,
        )

    files = await itgs.files()
    key_reader = io.BytesIO()
    try:
        found = await files.download(
            key_reader, bucket=files.default_bucket, key=s3_key, sync=True
        )
    except Exception as e:
        return GetJournalClientKeyResultS3Error(
            type="s3_error",
            user_sub=user_sub,
            journal_client_key_uid=journal_client_key_uid,
            exc=e,
        )

    if not found:
        return GetJournalClientKeyResultLost(
            type="lost",
            user_sub=user_sub,
            journal_client_key_uid=journal_client_key_uid,
        )

    key_data = key_reader.getvalue()
    try:
        key = cryptography.fernet.Fernet(key_data)
    except Exception as e:
        return GetJournalClientKeyResultParseError(
            type="parse_error",
            user_sub=user_sub,
            journal_client_key_uid=journal_client_key_uid,
            exc=e,
        )

    return GetJournalClientKeyResultSuccess(
        type="success",
        user_sub=user_sub,
        journal_client_key_uid=journal_client_key_uid,
        platform=platform,
        journal_client_key=key,
        journal_client_key_data=key_data,
    )


@dataclass
class CreateJournalClientKeyResultSuccess:
    type: Literal["success"]
    """
    - `success`: We successfully created the journal client key
    """
    user_sub: str
    """The sub of the user the key was created for"""
    journal_client_key_uid: str
    """The uid of the key created"""
    platform: str
    """The platform the key was generated for; useful as a sanity check"""
    journal_client_key: cryptography.fernet.Fernet
    """The journal client key data, wrapped with the appropriate way to use it"""
    journal_client_key_data: bytes
    """The raw journal client key data"""


@dataclass
class CreateJournalClientKeyResultS3Error:
    type: Literal["s3_error"]
    """
    - `s3_error`: There was an error uploading the journal client key to s3
    """
    user_sub: str
    """The sub of the user the key was created for"""
    exc: Exception
    """The exception that was raised while uploading the key"""


@dataclass
class CreateJournalClientKeyResultUserNotFound:
    type: Literal["user_not_found"]
    """
    - `user_not_found`: There is no user with the given sub
    """
    user_sub: str
    """The sub of the user the key was created for"""


CreateJournalClientKeyResult = Union[
    CreateJournalClientKeyResultSuccess,
    CreateJournalClientKeyResultS3Error,
    CreateJournalClientKeyResultUserNotFound,
]


async def create_journal_client_key(
    itgs: Itgs,
    /,
    *,
    user_sub: str,
    platform: str,
    visitor: str,
    now: float,
    new_key_data: bytes,
) -> CreateJournalClientKeyResult:
    """Creates a new journal client key for the user with the given sub

    Args:
        itgs (Itgs): the integrations to (re)use
        user_sub (str): the sub of the user to create the key for
        platform (str): the platform of the client requesting the key
        visitor (str): the uid of the visitor requesting the key
        now (float): the current time in seconds since the epoch
        new_key_data (bytes): The key data to use for the new key; usually
            this is selected as a side effect of a key generation function
    """
    new_fernet_key = cryptography.fernet.Fernet(new_key_data)
    new_key_uid = f"oseh_ujck_{secrets.token_urlsafe(16)}"
    new_s3_uid = f"oseh_s3f_{secrets.token_urlsafe(16)}"
    new_s3_key = f"s3_files/journals/keys/client/{new_key_uid}"

    files = await itgs.files()
    try:
        await files.upload(
            io.BytesIO(new_key_data),
            bucket=files.default_bucket,
            key=new_s3_key,
            sync=True,
        )
    except Exception as e:
        return CreateJournalClientKeyResultS3Error(
            type="s3_error",
            user_sub=user_sub,
            exc=e,
        )

    conn = await itgs.conn()
    cursor = conn.cursor()

    response = await cursor.executemany3(
        (
            (
                """
INSERT INTO s3_files (
    uid, key, file_size, content_type, created_at
)
SELECT
    ?, ?, ?, ?, ?
                """,
                (
                    new_s3_uid,
                    new_s3_key,
                    len(new_key_data),
                    "application/octet-stream",
                    now,
                ),
            ),
            (
                """
INSERT INTO user_journal_client_keys (
    uid, user_id, visitor_id, s3_file_id, platform, created_at, revoked_at
)
SELECT
    ?, users.id, visitors.id, s3_files.id, ?, ?, NULL
FROM users, s3_files
LEFT OUTER JOIN visitors ON visitors.uid = ?
WHERE
    users.sub = ?
    AND s3_files.uid = ?
                """,
                (new_key_uid, platform, now, visitor, user_sub, new_s3_uid),
            ),
        )
    )

    if response[0].rows_affected is None or response[0].rows_affected < 1:
        assert (
            response[1].rows_affected is None or response[1].rows_affected < 1
        ), response
        await files.delete(bucket=files.default_bucket, key=new_s3_key)
        return CreateJournalClientKeyResultUserNotFound(
            type="user_not_found", user_sub=user_sub
        )

    if response[1].rows_affected is None or response[1].rows_affected < 1:
        await files.delete(bucket=files.default_bucket, key=new_s3_key)
        await cursor.execute("DELETE FROM s3_files WHERE uid = ?", (new_s3_uid,))
        return CreateJournalClientKeyResultUserNotFound(
            type="user_not_found", user_sub=user_sub
        )

    return CreateJournalClientKeyResultSuccess(
        type="success",
        user_sub=user_sub,
        journal_client_key_uid=new_key_uid,
        platform=platform,
        journal_client_key=new_fernet_key,
        journal_client_key_data=new_key_data,
    )
