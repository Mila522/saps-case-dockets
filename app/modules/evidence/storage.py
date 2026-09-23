"""Private local storage adapter. Never mount this directory as static content."""
import hashlib
import logging
import re
import uuid

from fastapi import HTTPException

from app.core.config import settings


class EvidenceStorage:
    def __init__(self):
        self.root = settings.evidence_storage_path.resolve()
        self.max_bytes = settings.evidence_max_file_bytes

    def path(self, key):
        if not re.fullmatch(r'[0-9a-f]{32}', key):
            raise HTTPException(503, 'Evidence storage unavailable')
        path = self.root / key
        if path.is_symlink() or path.resolve().parent != self.root:
            raise HTTPException(503, 'Evidence storage unavailable')
        return path

    def save(self, stream):
        key = uuid.uuid4().hex
        path = self.path(key)
        created = False
        try:
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
            size, digest = 0, hashlib.sha256()
            with path.open('xb') as output:
                created = True
                while chunk := stream.read(64 * 1024):
                    size += len(chunk)
                    if size > self.max_bytes:
                        raise HTTPException(413, 'Evidence file exceeds the configured size limit')
                    digest.update(chunk)
                    output.write(chunk)
            if not size:
                raise HTTPException(422, 'Evidence file cannot be empty')
            return key, size, digest.hexdigest()
        except Exception as exc:
            if created:
                try:
                    self.discard(key)
                except OSError:
                    logging.getLogger(__name__).error('Evidence object cleanup failed; reconciliation required')
            if isinstance(exc, OSError):
                raise HTTPException(503, 'Evidence storage unavailable') from None
            raise

    def discard(self, key):
        self.path(key).unlink(missing_ok=True)

    def read_verified(self, row):
        try:
            with self.path(row.storage_key).open('rb') as source:
                data = source.read(self.max_bytes + 1)
        except OSError:
            raise HTTPException(503, 'Evidence file unavailable') from None
        if len(data) > self.max_bytes or len(data) != row.file_size_bytes or hashlib.sha256(data).hexdigest() != row.sha256_hash:
            raise HTTPException(409, 'Evidence file integrity check failed')
        return data
