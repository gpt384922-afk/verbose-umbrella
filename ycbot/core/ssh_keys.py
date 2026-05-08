from __future__ import annotations

import subprocess
from dataclasses import dataclass

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519


@dataclass(slots=True)
class SshKeyPair:
    username: str
    public_key: str
    private_key: str
    metadata_value: str


def generate_ssh_keypair(username: str) -> SshKeyPair:
    username = username.strip()
    if not username:
        raise ValueError("ssh username must not be empty")

    private = ed25519.Ed25519PrivateKey.generate()
    public_key = private.public_key().public_bytes(
        encoding=serialization.Encoding.OpenSSH,
        format=serialization.PublicFormat.OpenSSH,
    ).decode("ascii")
    private_key = private.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.OpenSSH,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")

    return SshKeyPair(
        username=username,
        public_key=public_key,
        private_key=private_key,
        metadata_value=f"{username}:{public_key}",
    )
