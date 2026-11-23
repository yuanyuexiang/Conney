import imaplib
import ssl
from base64 import b64encode
from itertools import groupby
from typing import Literal

ConnectionResult = Literal["ok", "auth_failed", "folder_unavailable", "tls_failed", "unreachable"]


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
