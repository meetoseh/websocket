from dataclasses import dataclass
from typing import Dict
import cryptography.fernet


@dataclass
class JournalChatData:
    journal_entry_uid: str
    """The UID of the journal entry the chat is within"""

    journal_chat_uid: str
    """The UID of the journal chat"""

    journal_client_key_uid: str
    """The UID of the journal client key that will be used as an additional layer
    of encryption when communicating between the server and the client
    """

    user_sub: str
    """The sub of the user who owns the journal"""

    journal_client_key: cryptography.fernet.Fernet
    """The wrapped Fernet key data for the journal client key"""

    journal_master_keys: Dict[str, cryptography.fernet.Fernet]
    """The journal master keys that we can use for decrypting internal communication"""
