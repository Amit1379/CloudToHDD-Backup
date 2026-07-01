"""Credential and sensitive config protection."""

from __future__ import annotations

import base64
import getpass
import hashlib
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes


def _derive_key(password: str, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=480_000,
    )
    return base64.urlsafe_b64encode(kdf.derive(password.encode("utf-8")))


def encrypt_text(plaintext: str, password: str, salt: bytes | None = None) -> tuple[bytes, bytes]:
    salt = salt or os.urandom(16)
    fernet = Fernet(_derive_key(password, salt))
    token = fernet.encrypt(plaintext.encode("utf-8"))
    return salt, token


def decrypt_text(token: bytes, password: str, salt: bytes) -> str:
    fernet = Fernet(_derive_key(password, salt))
    try:
        return fernet.decrypt(token).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("Invalid password or corrupted encrypted data.") from exc


def prompt_password(confirm: bool = False) -> str:
    while True:
        password = getpass.getpass("Enter encryption password: ")
        if not password:
            print("Password cannot be empty.")
            continue
        if confirm:
            again = getpass.getpass("Confirm encryption password: ")
            if password != again:
                print("Passwords do not match. Try again.")
                continue
        return password


def secure_delete_file(path: Path, passes: int = 1) -> None:
    if not path.exists() or not path.is_file():
        return
    size = path.stat().st_size
    with path.open("r+b") as handle:
        for _ in range(passes):
            handle.seek(0)
            handle.write(os.urandom(size))
            handle.flush()
            os.fsync(handle.fileno())
    path.unlink(missing_ok=True)


def hash_password_fingerprint(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()[:16]
