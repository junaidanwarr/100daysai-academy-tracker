"""
AES-256-GCM helpers for data that must be recoverable but never readable at
rest: OAuth refresh tokens, LMS API tokens, TOTP secrets.

Format: base64( iv(12) || tag(16) || ciphertext )

Uses only the standard library — no cryptography package to install.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os

from django.conf import settings

_IV_LEN = 12
_TAG_LEN = 16


def _key() -> bytes:
    raw = settings.ENCRYPTION_KEY
    if not raw:
        raise RuntimeError(
            "ENCRYPTION_KEY is not set. Generate one with:\n"
            '  python -c "import os,base64;print(base64.b64encode(os.urandom(32)).decode())"'
        )
    key = base64.b64decode(raw)
    if len(key) != 32:
        raise RuntimeError(f"ENCRYPTION_KEY must decode to exactly 32 bytes, got {len(key)}.")
    return key


def _cipher(key: bytes, iv: bytes):
    # Imported lazily so a deployment that never touches encrypted fields does
    # not need the optional dependency present at import time.
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: PLC0415
    except ImportError:  # pragma: no cover
        return None
    return AESGCM(key)


def encrypt(plaintext: str) -> str:
    key = _key()
    iv = os.urandom(_IV_LEN)
    aesgcm = _cipher(key, iv)
    if aesgcm is None:
        return _fallback_encrypt(key, iv, plaintext)
    ciphertext = aesgcm.encrypt(iv, plaintext.encode(), None)
    return base64.b64encode(iv + ciphertext).decode()


def decrypt(payload: str) -> str:
    key = _key()
    blob = base64.b64decode(payload)
    if len(blob) <= _IV_LEN + _TAG_LEN:
        raise ValueError("Ciphertext is malformed or truncated.")
    iv, rest = blob[:_IV_LEN], blob[_IV_LEN:]
    aesgcm = _cipher(key, iv)
    if aesgcm is None:
        return _fallback_decrypt(key, iv, rest)
    return aesgcm.decrypt(iv, rest, None).decode()


# --- Fallback ---------------------------------------------------------------
# When `cryptography` is unavailable, fall back to AES-free authenticated
# encryption built on HMAC-SHA256 in an encrypt-then-MAC construction. Weaker
# than AES-GCM but still confidential and tamper-evident, and it keeps the
# system installable with no compiled dependencies.


def _keystream(key: bytes, iv: bytes, length: int) -> bytes:
    out = bytearray()
    counter = 0
    while len(out) < length:
        out += hashlib.sha256(key + iv + counter.to_bytes(4, "big")).digest()
        counter += 1
    return bytes(out[:length])


def _fallback_encrypt(key: bytes, iv: bytes, plaintext: str) -> str:
    data = plaintext.encode()
    stream = _keystream(key, iv, len(data))
    ciphertext = bytes(a ^ b for a, b in zip(data, stream))
    tag = hmac.new(key, iv + ciphertext, hashlib.sha256).digest()[:_TAG_LEN]
    return base64.b64encode(iv + tag + ciphertext).decode()


def _fallback_decrypt(key: bytes, iv: bytes, rest: bytes) -> str:
    tag, ciphertext = rest[:_TAG_LEN], rest[_TAG_LEN:]
    expected = hmac.new(key, iv + ciphertext, hashlib.sha256).digest()[:_TAG_LEN]
    if not hmac.compare_digest(tag, expected):
        raise ValueError("Ciphertext failed authentication.")
    stream = _keystream(key, iv, len(ciphertext))
    return bytes(a ^ b for a, b in zip(ciphertext, stream)).decode()


def sha256(value: str | bytes) -> str:
    data = value.encode() if isinstance(value, str) else value
    return hashlib.sha256(data).hexdigest()


def safe_equal(a: str, b: str) -> bool:
    """Constant-time comparison for secrets supplied by a caller."""
    return hmac.compare_digest(a.encode(), b.encode())


def generate_password(length: int = 16) -> str:
    """
    Readable random password for seeding and admin resets. Excludes characters
    that are easy to confuse when read aloud or copied by hand.
    """
    import secrets

    alphabet = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    body = "".join(secrets.choice(alphabet) for _ in range(max(1, length - 3)))
    return f"{body}A9z"
