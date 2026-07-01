"""EXIF GPS verification — confirm photo location data survived copy."""

from __future__ import annotations

import json
import logging
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .utils import should_exclude, utc_now_iso

logger = logging.getLogger("cloudtohdd.exif_gps")

IMAGE_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".heif",
    ".heic",
    ".tif",
    ".tiff",
    ".png",
    ".webp",
    ".dng",
    ".hif",
}

_heif_registered = False


def _ensure_heif_support() -> None:
    global _heif_registered
    if _heif_registered:
        return
    try:
        import pillow_heif

        pillow_heif.register_heif_opener()
        _heif_registered = True
    except ImportError:
        logger.debug("pillow-heif not installed — HEIC GPS read may be limited")


@dataclass
class GpsCoords:
    latitude: float
    longitude: float


@dataclass
class GpsLostFile:
    relative_path: str
    source_lat: float
    source_lon: float
    reason: str  # missing_in_dest | gps_stripped | dest_missing


@dataclass
class GpsVerificationReport:
    provider: str
    source_root: Path
    destination: Path
    images_scanned: int = 0
    source_with_gps: int = 0
    dest_with_gps: int = 0
    gps_preserved: int = 0
    gps_lost: list[GpsLostFile] = field(default_factory=list)
    no_gps_in_source: int = 0

    @property
    def gps_lost_count(self) -> int:
        return len(self.gps_lost)

    @property
    def preservation_percent(self) -> float:
        if self.source_with_gps == 0:
            return 100.0
        return (self.gps_preserved / self.source_with_gps) * 100.0

    @property
    def passed(self) -> bool:
        return self.gps_lost_count == 0


def _is_image(path: Path) -> bool:
    return path.suffix.lower() in IMAGE_EXTENSIONS


def _dms_to_decimal(values: tuple, ref: str) -> float:
    """Convert EXIF degrees/minutes/seconds to decimal degrees."""
    degrees = float(values[0])
    minutes = float(values[1]) / 60.0
    seconds = float(values[2]) / 3600.0
    decimal = degrees + minutes + seconds
    if ref in ("S", "W"):
        decimal = -decimal
    return decimal


def get_gps_coords(path: Path) -> GpsCoords | None:
    """Read GPS latitude/longitude from EXIF embedded in an image file."""
    if not path.is_file() or not _is_image(path):
        return None

    _ensure_heif_support()

    try:
        from PIL import Image
        from PIL.ExifTags import IFD
    except ImportError:
        logger.warning("Pillow not installed — cannot read EXIF GPS")
        return None

    try:
        with Image.open(path) as img:
            exif = img.getexif()
            if not exif:
                return None
            gps_ifd = exif.get_ifd(IFD.GPSInfo)
            if not gps_ifd:
                return None

            lat = gps_ifd.get(2)
            lat_ref = gps_ifd.get(1, "N")
            lon = gps_ifd.get(4)
            lon_ref = gps_ifd.get(3, "E")

            if lat is None or lon is None:
                return None

            return GpsCoords(
                latitude=_dms_to_decimal(lat, str(lat_ref)),
                longitude=_dms_to_decimal(lon, str(lon_ref)),
            )
    except OSError as exc:
        logger.debug("Could not read EXIF from %s: %s", path, exc)
        return None


def has_gps(path: Path) -> bool:
    return get_gps_coords(path) is not None


def gps_coords_match(
    a: GpsCoords,
    b: GpsCoords,
    *,
    tolerance_meters: float = 50.0,
) -> bool:
    """True if two GPS points are within tolerance (haversine distance)."""
    radius = 6_371_000.0
    lat1, lon1 = math.radians(a.latitude), math.radians(a.longitude)
    lat2, lon2 = math.radians(b.latitude), math.radians(b.longitude)
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    distance = 2 * radius * math.asin(math.sqrt(h))
    return distance <= tolerance_meters


def _iter_images(
    root: Path,
    exclude_patterns: list[str],
    include_paths: list[str] | None = None,
) -> list[Path]:
    files: list[Path] = []
    if include_paths:
        bases = [root / p for p in include_paths]
    else:
        bases = [root]

    for base in bases:
        if not base.exists():
            continue
        if base.is_file() and _is_image(base):
            if not should_exclude(base.name, exclude_patterns):
                files.append(base)
            continue
        for path in base.rglob("*"):
            if not path.is_file() or not _is_image(path):
                continue
            if should_exclude(path.name, exclude_patterns):
                continue
            files.append(path)
    return files


def verify_gps_preserved(
    provider: str,
    source_root: Path,
    destination: Path,
    exclude_patterns: list[str],
    include_paths: list[str] | None = None,
    *,
    tolerance_meters: float = 50.0,
    image_paths: list[Path] | None = None,
    progress_callback=None,
) -> GpsVerificationReport:
    """
    Compare GPS EXIF in source images vs backup copies.
    Only images that had GPS in the source are required to retain it.
    """
    include_paths = include_paths or []
    report = GpsVerificationReport(
        provider=provider,
        source_root=source_root,
        destination=destination,
    )

    if image_paths is not None:
        images = [p for p in image_paths if p.is_file() and _is_image(p)]
    else:
        images = _iter_images(source_root, exclude_patterns, include_paths)
    report.images_scanned = len(images)

    for idx, src_path in enumerate(images):
        rel = src_path.relative_to(source_root).as_posix()
        if progress_callback:
            progress_callback(idx + 1, len(images), rel)

        src_gps = get_gps_coords(src_path)
        if src_gps is None:
            report.no_gps_in_source += 1
            continue

        report.source_with_gps += 1
        dest_path = destination / Path(rel)

        if not dest_path.is_file():
            report.gps_lost.append(
                GpsLostFile(rel, src_gps.latitude, src_gps.longitude, "missing_in_dest")
            )
            continue

        dest_gps = get_gps_coords(dest_path)
        if dest_gps is None:
            report.gps_lost.append(
                GpsLostFile(rel, src_gps.latitude, src_gps.longitude, "gps_stripped")
            )
            continue

        report.dest_with_gps += 1
        if gps_coords_match(src_gps, dest_gps, tolerance_meters=tolerance_meters):
            report.gps_preserved += 1
        else:
            report.gps_lost.append(
                GpsLostFile(
                    rel,
                    src_gps.latitude,
                    src_gps.longitude,
                    f"coords_mismatch dest=({dest_gps.latitude:.6f},{dest_gps.longitude:.6f})",
                )
            )

    logger.info(
        "[%s] GPS: %d/%d photos with location preserved (%.1f%%), %d lost, %d had no GPS",
        provider,
        report.gps_preserved,
        report.source_with_gps,
        report.preservation_percent,
        report.gps_lost_count,
        report.no_gps_in_source,
    )
    return report


def save_gps_report(report: GpsVerificationReport, logs_dir: Path) -> Path:
    logs_dir.mkdir(parents=True, exist_ok=True)
    path = logs_dir / f"gps_report_{report.provider}_{utc_now_iso().replace(':', '-')}.json"
    payload = {
        "timestamp": utc_now_iso(),
        "provider": report.provider,
        "summary": {
            "images_scanned": report.images_scanned,
            "source_with_gps": report.source_with_gps,
            "dest_with_gps": report.dest_with_gps,
            "gps_preserved": report.gps_preserved,
            "gps_lost_count": report.gps_lost_count,
            "preservation_percent": report.preservation_percent,
            "passed": report.passed,
        },
        "lost_files": [asdict(f) for f in report.gps_lost[:500]],
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path
