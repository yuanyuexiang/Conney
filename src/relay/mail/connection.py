import imaplib
import ssl
from base64 import b64encode
from itertools import groupby
from typing import Literal

NETEASE_HOST = "imap.163.com"
ConnectionResult = Literal[
    "ok", "auth_failed", "client_id_failed", "folder_unavailable", "tls_failed", "unreachable"
]


def _encode_folder(folder: str) -> str:
    """Encode a mailbox name using IMAP4rev1 modified UTF-7 and quote it."""
    encoded = []
    for is_ascii, run in groupby(folder, key=str.isascii):
        segment = "".join(run)
        if is_ascii:
            encoded.append(segment.replace("&", "&-"))
        else:
            data = (
                b64encode(segment.encode("utf-16be")).decode("ascii").rstrip("=").replace("/", ",")
            )
            encoded.append(f"&{data}-")
    name = "".join(encoded).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{name}"'


def identify_client(client: imaplib.IMAP4, host: str) -> bool:
    if host != NETEASE_HOST:
        return True
    # NetEase expects the IMAP ID extension after login, before EXAMINE or UID commands.
    try:
        status, _ = client.xatom("ID", '("name" "Relay" "version" "0.1.0")')
    except imaplib.IMAP4.abort:
        raise
    except imaplib.IMAP4.error:
        return False
    return status == "OK"


def check_connection(address: str, host: str, folder: str, password: str) -> ConnectionResult:
    context = ssl.create_default_context()
    try:
        with imaplib.IMAP4_SSL(host, 993, ssl_context=context, timeout=8) as client:
            try:
                client.login(address, password)
            except imaplib.IMAP4.abort:
                return "unreachable"
            except imaplib.IMAP4.error:
                return "auth_failed"

            if not identify_client(client, host):
                return "client_id_failed"

            try:
                kind, _ = client.select(_encode_folder(folder), readonly=True)
            except imaplib.IMAP4.abort:
                return "unreachable"
            except imaplib.IMAP4.error:
                return "folder_unavailable"
            return "ok" if kind == "OK" else "folder_unavailable"
    except ssl.SSLError:
        return "tls_failed"
    except (OSError, imaplib.IMAP4.error, UnicodeEncodeError):
        return "unreachable"
