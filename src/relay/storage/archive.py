import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile

MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024


class ArchiveIntegrityError(OSError):
    """A file is missing or does not match the recorded hash."""


@dataclass(frozen=True)
class ArchivedObject:
    sha256: str
    size_bytes: int


class Archive:
    def __init__(self, root: Path) -> None:
        self.root = root

    def store(self, content: bytes) -> ArchivedObject:
        if not content or len(content) > MAX_ATTACHMENT_BYTES:
            raise ValueError("Attachment is empty or exceeds the archive size limit")
        digest = hashlib.sha256(content).hexdigest()
        self.root.mkdir(mode=0o750, parents=True, exist_ok=True)
        path = self.root / digest
        temporary: Path | None = None
        try:
            with NamedTemporaryFile(dir=self.root, prefix=".incoming-", delete=False) as output:
                temporary = Path(output.name)
                output.write(content)
                output.flush()
                os.fsync(output.fileno())
                os.fchmod(output.fileno(), 0o440)
            try:
                os.link(temporary, path)
            except FileExistsError:
                self.verified_path(digest, len(content))
            # A concurrent writer can have linked this name without flushing the directory yet.
            directory = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return ArchivedObject(digest, len(content))

    def verified_path(self, digest: str, size_bytes: int) -> Path:
        if (
            re.fullmatch(r"[a-f0-9]{64}", digest) is None
            or not 0 < size_bytes <= MAX_ATTACHMENT_BYTES
        ):
            raise ArchiveIntegrityError("Invalid attachment metadata")
        path = self.root / digest
        try:
            with path.open("rb") as source:
                if os.fstat(source.fileno()).st_size != size_bytes:
                    raise ArchiveIntegrityError("Attachment size differs from the archive record")
                if hashlib.file_digest(source, "sha256").hexdigest() != digest:
                    raise ArchiveIntegrityError(
                        "Attachment content differs from the archive record"
                    )
        except FileNotFoundError as error:
            raise ArchiveIntegrityError("Attachment is missing") from error
        return path


def archive_from_environment() -> Archive:
    return Archive(Path(os.environ.get("RELAY_ARCHIVE_DIR", "data/attachments")))
