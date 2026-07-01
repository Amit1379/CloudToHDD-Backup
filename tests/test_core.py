"""Core reliability tests for commercial release."""

from __future__ import annotations

import tempfile
import unittest
import os
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import yaml

from src.backup_engine import BackupEngine
from src.iphone_usb import IPhoneDetection
from src.metadata import copy_file_preserve_metadata, metadata_matches
from src.providers.base import BackupResult
from src.utils import sha256_file
from src.verifier import VerificationReport

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def temp_file(name: str) -> Path:
    return PROJECT_ROOT / f".test_{uuid4().hex}_{name}"


class TestSha256(unittest.TestCase):
    def test_sha256_file(self) -> None:
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(b"hello backup")
            path = Path(tmp.name)
        try:
            digest = sha256_file(path)
            self.assertEqual(len(digest), 64)
        finally:
            path.unlink(missing_ok=True)


class TestBackupResult(unittest.TestCase):
    def test_zero_copy_fails(self) -> None:
        r = BackupResult("onedrive", "robocopy", "/dest")
        self.assertFalse(r.success)

    def test_dry_run_ok_without_copy(self) -> None:
        r = BackupResult("onedrive", "robocopy", "/dest", dry_run=True)
        self.assertTrue(r.success)


class TestVerifier(unittest.TestCase):
    def test_empty_source_never_passes(self) -> None:
        report = VerificationReport("test", Path("."), Path("."), total_source_files=0)
        self.assertFalse(report.passed)
        self.assertEqual(report.completeness_percent, 100.0)


class TestMetadataCopy(unittest.TestCase):
    def test_metadata_verification_ignores_access_time_by_default(self) -> None:
        source = temp_file("source.txt")
        dest = temp_file("dest.txt")
        try:
            source.write_text("enterprise backup", encoding="utf-8")
            copy_file_preserve_metadata(source, dest)

            stat = dest.stat()
            os.utime(dest, (stat.st_atime + 3600, stat.st_mtime))

            self.assertTrue(metadata_matches(source, dest))
            self.assertFalse(metadata_matches(source, dest, compare_atime=True))
        finally:
            source.unlink(missing_ok=True)
            dest.unlink(missing_ok=True)
            (dest.parent / f".{dest.name}.partial").unlink(missing_ok=True)

    def test_copy_replaces_destination_without_leaving_partial_file(self) -> None:
        source = temp_file("source.txt")
        dest = temp_file("dest.txt")
        partial = dest.parent / f".{dest.name}.partial"
        try:
            source.write_text("new complete contents", encoding="utf-8")
            dest.write_text("old", encoding="utf-8")

            copy_file_preserve_metadata(source, dest)

            self.assertEqual(dest.read_text(encoding="utf-8"), "new complete contents")
            self.assertFalse(partial.exists())
        finally:
            source.unlink(missing_ok=True)
            dest.unlink(missing_ok=True)
            partial.unlink(missing_ok=True)


class TestRunLock(unittest.TestCase):
    def test_run_lock_blocks_overlapping_backup(self) -> None:
        root = PROJECT_ROOT
        lock = root / ".backup.lock"
        lock.unlink(missing_ok=True)
        engine = BackupEngine.__new__(BackupEngine)
        try:
            with engine._exclusive_run_lock(root):
                with self.assertRaises(RuntimeError):
                    with engine._exclusive_run_lock(root):
                        pass
            self.assertFalse((root / ".backup.lock").exists())
        finally:
            lock.unlink(missing_ok=True)


class TestUnavailableIPhone(unittest.TestCase):
    def _write_config(self, root: Path, *, strict: bool) -> Path:
        config_path = root / "config.yaml"
        config = {
            "backup": {
                "destination_root": str(root / "backup"),
                "layout": "flat",
                "dry_run": False,
                "block_unavailable_sources": strict,
                "verify_checksums": False,
                "preserve_metadata": {"verify_gps": False, "verify_timestamps": False},
            },
            "safety": {"auto_certify_after_backup": False},
            "providers": {"iphone": {"enabled": True, "usb_path": ""}},
        }
        config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
        return config_path

    def _write_icloud_fallback_config(self, root: Path) -> Path:
        icloud_source = root / "iCloudPhotos" / "Photos"
        icloud_source.mkdir(parents=True)
        (icloud_source / "photo.jpg").write_bytes(b"fake photo bytes")
        config_path = root / "config.yaml"
        config = {
            "backup": {
                "destination_root": str(root / "backup"),
                "layout": "flat",
                "dry_run": True,
                "verify_checksums": False,
                "preserve_metadata": {"verify_gps": False, "verify_timestamps": False},
            },
            "safety": {"auto_certify_after_backup": False},
            "providers": {
                "iphone": {"enabled": True, "usb_path": ""},
                "icloud": {
                    "enabled": False,
                    "method": "sync_folder",
                    "sync_folder": str(icloud_source),
                    "exclude_patterns": [],
                },
            },
        }
        config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
        return config_path

    def test_unavailable_iphone_does_not_abort_other_runs_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = self._write_config(Path(tmp), strict=False)
            detection = IPhoneDetection(status="not_found", message="not connected")
            with patch("src.providers.iphone.detect_iphone_usb", return_value=detection), patch(
                "src.iphone_usb.detect_iphone_usb", return_value=detection
            ):
                results = BackupEngine(config_path).run(["iphone"], skip_preflight=True)

            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].provider, "iphone")
            self.assertFalse(results[0].success)
            self.assertIn("iPhone not found", results[0].errors[0])
            self.assertEqual(results[0].completeness_percent, 0.0)

    def test_legacy_strict_flag_does_not_abort_iphone_only_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = self._write_config(Path(tmp), strict=True)
            detection = IPhoneDetection(status="not_found", message="not connected")
            with patch("src.providers.iphone.detect_iphone_usb", return_value=detection), patch(
                "src.iphone_usb.detect_iphone_usb", return_value=detection
            ):
                results = BackupEngine(config_path).run(["iphone"], skip_preflight=True)

            self.assertEqual(len(results), 1)
            self.assertFalse(results[0].success)

    def test_failed_iphone_is_not_safety_certified(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = self._write_config(root, strict=False)
            config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
            config["safety"]["auto_certify_after_backup"] = True
            config_path.write_text(yaml.safe_dump(config), encoding="utf-8")

            detection = IPhoneDetection(
                status="connected_locked",
                message="iPhone connected but storage is not accessible.",
            )
            with patch("src.providers.iphone.detect_iphone_usb", return_value=detection), patch(
                "src.iphone_usb.detect_iphone_usb", return_value=detection
            ):
                results = BackupEngine(config_path).run(["iphone"], skip_preflight=True)

            self.assertEqual(results[0].method, "locked")
            self.assertEqual(results[0].completeness_percent, 0.0)
            self.assertIs(results[0].safe_to_delete_source, False)
            self.assertFalse((root / "backup" / "Logs" / "SAFETY_CERTIFICATE_LATEST.txt").exists())

    def test_locked_iphone_uses_icloud_photos_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = self._write_icloud_fallback_config(Path(tmp))
            detection = IPhoneDetection(
                status="connected_locked",
                message="iPhone connected but storage is not accessible.",
            )
            with patch("src.iphone_usb.detect_iphone_usb", return_value=detection):
                results = BackupEngine(config_path).run(skip_preflight=True)

            self.assertEqual([r.provider for r in results], ["icloud"])
            self.assertTrue(results[0].success)
            self.assertGreater(results[0].files_copied, 0)


if __name__ == "__main__":
    unittest.main()
