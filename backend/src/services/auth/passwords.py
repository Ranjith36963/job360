"""Argon2id password hashing.

Uses ``argon2-cffi`` with OWASP-recommended defaults (time_cost=3, memory=64 MiB,
parallelism=4, hash_len=32). These defaults tune against modern desktop CPUs;
raising ``time_cost`` is an additive change (old hashes still verify — argon2
embeds parameters in the PHC string).
"""
from __future__ import annotations

from typing import cast

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4, hash_len=32)


def hash_password(plaintext: str) -> str:
    return cast(str, _hasher.hash(plaintext))


def verify_password(stored_hash: str, plaintext: str) -> bool:
    try:
        return cast(bool, _hasher.verify(stored_hash, plaintext))
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False
