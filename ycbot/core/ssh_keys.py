from __future__ import annotations

import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


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

    with tempfile.TemporaryDirectory(prefix="ychunter-ssh-") as tmpdir:
        key_path = Path(tmpdir) / "id_ed25519"
        subprocess.run(
            [
                "ssh-keygen",
                "-t",
                "ed25519",
                "-N",
                "",
                "-C",
                f"ychunter-{username}",
                "-f",
                str(key_path),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        public_key = key_path.with_suffix(".pub").read_text(encoding="utf-8").strip()
        private_key = key_path.read_text(encoding="utf-8")

    return SshKeyPair(
        username=username,
        public_key=public_key,
        private_key=private_key,
        metadata_value=f"{username}:{public_key}",
    )
