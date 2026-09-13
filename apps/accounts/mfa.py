"""
TOTP second factor (specification section 21: "Multi-factor authentication for
administrators"). Secrets are stored encrypted, recovery codes are stored
hashed and are single use.
"""

from __future__ import annotations

import base64
import io
import secrets

import pyotp
import qrcode

from apps.core.crypto import decrypt, encrypt, sha256

ISSUER = "100DaysAI Academy"

# One step of clock skew each way; enough for a phone that is slightly off.
VALID_WINDOW = 1


def generate_secret() -> str:
    return pyotp.random_base32()


def build_otpauth_url(email: str, secret: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name=ISSUER)


def build_qr_data_url(otpauth_url: str) -> str:
    img = qrcode.make(otpauth_url)
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode()
    return f"data:image/png;base64,{encoded}"


def verify_totp(token: str, secret: str) -> bool:
    # pyotp raises on malformed input rather than returning False.
    try:
        return pyotp.TOTP(secret).verify(token.replace(" ", ""), valid_window=VALID_WINDOW)
    except Exception:  # noqa: BLE001
        return False


def encrypt_secret(secret: str) -> str:
    return encrypt(secret)


def decrypt_secret(ciphertext: str) -> str:
    return decrypt(ciphertext)


def generate_recovery_codes(count: int = 10) -> tuple[list[str], list[str]]:
    """Returns the plaintext codes to show once, plus the hashes to store."""
    codes = []
    for _ in range(count):
        raw = secrets.token_hex(5).upper()
        codes.append(f"{raw[:5]}-{raw[5:10]}")
    return codes, [sha256(code) for code in codes]


def match_recovery_code(supplied: str, hashes: list[str]) -> int:
    """Index of the matching hash, or -1."""
    digest = sha256(supplied.strip().upper())
    return hashes.index(digest) if digest in hashes else -1
