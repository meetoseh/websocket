import secrets


def gen_packet_uid() -> str:
    """Generates a unique packet uid"""
    return f"oseh_packet_{secrets.token_urlsafe(16)}"
