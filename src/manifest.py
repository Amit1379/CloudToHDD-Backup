"""Backup manifest for incremental sync and audit trail."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path

from .utils import sha256_file, utc_now_iso

logger = logging.getLogger("cloudtohdd.manifest")


@dataclass
class FileRecord:
    relative_path: str
    size: int
    mtime: float
    sha256: str | None = None
    last_backed_up: str | None = None


class ManifestStore:
    def __init__(self, manifest_path: Path):
        self.path = manifest_path
        self.records: dict[str, FileRecord] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            for key, value in data.get("files", {}).items():
                self.records[key] = FileRecord(**value)
        except (json.JSONDecodeError, TypeError, KeyError) as exc:
            logger.warning("Could not load manifest %s: %s", self.path, exc)
            corrupt_path = self.path.with_suffix(self.path.suffix + ".corrupt")
            try:
                self.path.replace(corrupt_path)
                logger.warning("Moved unreadable manifest to %s", corrupt_path)
            except OSError:
                logger.warning("Could not preserve corrupt manifest %s", self.path)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 1,
            "updated_at": utc_now_iso(),
            "files": {key: asdict(record) for key, record in self.records.items()},
        }
        temp_path = self.path.with_suffix(".tmp")
        temp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        with temp_path.open("r+", encoding="utf-8") as handle:
            handle.flush()
            try:
                import os

                os.fsync(handle.fileno())
            except OSError:
                pass
        temp_path.replace(self.path)

    def needs_backup(
        self,
        relative_path: str,
        size: int,
        mtime: float,
        verify_checksums: bool,
        source_path: Path | None = None,
    ) -> bool:
        record = self.records.get(relative_path)
        if record is None:
            return True
        if record.size != size or record.mtime != mtime:
            return True
        if verify_checksums and source_path and source_path.is_file():
            current_hash = sha256_file(source_path)
            return record.sha256 != current_hash
        return False

    def update_record(
        self,
        relative_path: str,
        size: int,
        mtime: float,
        source_path: Path | None = None,
        compute_hash: bool = True,
    ) -> None:
        file_hash = None
        if compute_hash and source_path and source_path.is_file():
            file_hash = sha256_file(source_path)
        self.records[relative_path] = FileRecord(
            relative_path=relative_path,
            size=size,
            mtime=mtime,
            sha256=file_hash,
            last_backed_up=utc_now_iso(),
        )
