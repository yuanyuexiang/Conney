import argparse
from datetime import UTC, datetime
from pathlib import Path

from relay.mail.intake import MAX_MESSAGE_BYTES, MailIdentity, receive
from relay.storage.archive import archive_from_environment


def main() -> None:
    arguments = argparse.ArgumentParser(description="Replay a local EML into the Relay archive")
    arguments.add_argument("eml", type=Path)
    arguments.add_argument("--uid", type=int, required=True)
    arguments.add_argument("--uidvalidity", type=int, default=1)
    arguments.add_argument("--folder", default="INBOX")
    flags = arguments.parse_args()
    with flags.eml.open("rb") as source:
        raw = source.read(MAX_MESSAGE_BYTES + 1)
    result = receive(
        raw,
        MailIdentity("local:eml", flags.folder, flags.uidvalidity, flags.uid, datetime.now(UTC)),
        archive_from_environment(),
    )
    print(f"mail_id={result.mail_id}; created={str(result.created).lower()}")


if __name__ == "__main__":
    main()
