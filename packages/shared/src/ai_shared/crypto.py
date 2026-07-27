"""Encryption service for sensitive columns.

Sensitive personal data (caller numbers, addresses, message bodies,
OAuth tokens) is stored encrypted at the application layer, with a
deterministic HMAC hash column alongside wherever equality lookup is
needed (e.g. "find calls from this number").

The interface is a service abstraction so the primitive can be swapped
(e.g. to a KMS-backed implementation) without touching call sites.

Format of ciphertext: ``v1:<base64(nonce | ciphertext | tag)>`` —
versioned so future key or algorithm rotation can coexist with old rows.
"""

import base64
import hashlib
import hmac
import os
from typing import Protocol

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_VERSION_PREFIX = "v1:"
_NONCE_BYTES = 12


class EncryptionError(Exception):
    """Raised when decryption fails (wrong key, corrupt data, bad format)."""


class EncryptionService(Protocol):
    """Application-level encryption for sensitive fields."""

    def encrypt(self, plaintext: str) -> str: ...

    def decrypt(self, ciphertext: str) -> str: ...

    def hash_for_lookup(self, value: str) -> str: ...


class AesGcmEncryptionService:
    """AES-256-GCM with an HMAC-SHA256 lookup hash.

    ``data_key`` and ``hash_key`` must be independent 32-byte keys
    (base64-encoded in configuration). The lookup hash is deterministic
    per value — required for equality search — and keyed, so raw values
    cannot be brute-forced from hashes without the key.
    """

    def __init__(self, *, data_key_b64: str, hash_key_b64: str) -> None:
        data_key = base64.b64decode(data_key_b64)
        hash_key = base64.b64decode(hash_key_b64)
        if len(data_key) != 32:
            raise ValueError("data key must be 32 bytes (base64-encoded)")
        if len(hash_key) != 32:
            raise ValueError("hash key must be 32 bytes (base64-encoded)")
        if data_key == hash_key:
            raise ValueError("data key and hash key must be independent")
        self._aead = AESGCM(data_key)
        self._hash_key = hash_key

    def encrypt(self, plaintext: str) -> str:
        nonce = os.urandom(_NONCE_BYTES)
        sealed = self._aead.encrypt(nonce, plaintext.encode("utf-8"), None)
        return _VERSION_PREFIX + base64.b64encode(nonce + sealed).decode("ascii")

    def decrypt(self, ciphertext: str) -> str:
        if not ciphertext.startswith(_VERSION_PREFIX):
            raise EncryptionError("unrecognized ciphertext format")
        try:
            raw = base64.b64decode(ciphertext[len(_VERSION_PREFIX) :])
            plaintext = self._aead.decrypt(raw[:_NONCE_BYTES], raw[_NONCE_BYTES:], None)
        except Exception as exc:
            raise EncryptionError("decryption failed") from exc
        return plaintext.decode("utf-8")

    def hash_for_lookup(self, value: str) -> str:
        digest = hmac.new(self._hash_key, value.encode("utf-8"), hashlib.sha256)
        return digest.hexdigest()


def normalize_phone(e164: str) -> str:
    """Canonicalize a phone number before hashing (strip formatting)."""
    return "".join(ch for ch in e164 if ch.isdigit() or ch == "+")


def last_four(value: str) -> str:
    digits = "".join(ch for ch in value if ch.isdigit())
    return digits[-4:] if len(digits) >= 4 else digits
