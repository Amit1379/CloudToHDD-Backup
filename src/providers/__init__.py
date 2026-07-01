"""Cloud provider adapters."""

from .android import AndroidProvider
from .base import BackupProvider, BackupResult
from .google_photos import GooglePhotosProvider
from .icloud import ICloudProvider
from .google_drive import GoogleDriveProvider
from .iphone import IPhoneProvider
from .onedrive import OneDriveProvider

__all__ = [
    "BackupProvider",
    "BackupResult",
    "IPhoneProvider",
    "AndroidProvider",
    "GooglePhotosProvider",
    "OneDriveProvider",
    "GoogleDriveProvider",
    "ICloudProvider",
]
