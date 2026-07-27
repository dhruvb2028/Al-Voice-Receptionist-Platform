"""Tests for the encryption service."""

import base64
import os

import pytest
from ai_shared.crypto import (
    AesGcmEncryptionService,
    EncryptionError,
    last_four,
    normalize_phone,
)


def _service() -> AesGcmEncryptionService:
    return AesGcmEncryptionService(
        data_key_b64=base64.b64encode(os.urandom(32)).decode(),
        hash_key_b64=base64.b64encode(os.urandom(32)).decode(),
    )


def test_roundtrip() -> None:
    svc = _service()
    sealed = svc.encrypt("+15551234567")
    assert sealed.startswith("v1:")
    assert svc.decrypt(sealed) == "+15551234567"


def test_ciphertexts_are_nondeterministic() -> None:
    svc = _service()
    assert svc.encrypt("same") != svc.encrypt("same")


def test_lookup_hash_is_deterministic_and_keyed() -> None:
    svc_a = _service()
    svc_b = _service()
    assert svc_a.hash_for_lookup("+15551234567") == svc_a.hash_for_lookup("+15551234567")
    assert svc_a.hash_for_lookup("+15551234567") != svc_b.hash_for_lookup("+15551234567")


def test_wrong_key_fails_closed() -> None:
    sealed = _service().encrypt("secret")
    with pytest.raises(EncryptionError):
        _service().decrypt(sealed)


def test_tampered_ciphertext_rejected() -> None:
    svc = _service()
    sealed = svc.encrypt("secret")
    tampered = sealed[:-4] + ("AAAA" if not sealed.endswith("AAAA") else "BBBB")
    with pytest.raises(EncryptionError):
        svc.decrypt(tampered)


def test_bad_format_rejected() -> None:
    with pytest.raises(EncryptionError):
        _service().decrypt("plaintext-or-unknown-format")


def test_identical_keys_rejected() -> None:
    key = base64.b64encode(os.urandom(32)).decode()
    with pytest.raises(ValueError, match="independent"):
        AesGcmEncryptionService(data_key_b64=key, hash_key_b64=key)


def test_phone_helpers() -> None:
    assert normalize_phone("+1 (555) 123-4567") == "+15551234567"
    assert last_four("+15551234567") == "4567"
    assert last_four("911") == "911"
