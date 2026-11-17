"""Local immutable artifacts and replaceable JSON exports."""

import hashlib
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from research_multiplier.storage import paths
from research_multiplier.policy.contracts import ArtifactRef
from research_multiplier.protocol.serialization import json_text


def write_bytes(path, data, *, external=False):
    """Publish a complete file on the same filesystem, safe under concurrent writers."""
    path = Path(path) if external else paths.validate_project_write(path)
    stream = NamedTemporaryFile(dir=path.parent, prefix=".rm-", delete=False)
    temporary = Path(stream.name)
    try:
        with stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def write_json(path, data, *, external=False):
    write_bytes(path, (json_text(data) + "\n").encode("utf-8"), external=external)


class Artifacts:
    def __init__(self, root, *, workspace=True):
        self.run_root = Path(root)
        shared = paths.shared_artifacts(self.run_root) if workspace else None
        self.root = shared if shared is not None else self.run_root

    def put(self, value, *, raw=False) -> ArtifactRef:
        data = value.encode("utf-8") if raw else json_text(value).encode("utf-8")
        return self.put_bytes(data)

    def put_bytes(self, data: bytes) -> ArtifactRef:
        paths.validate_project_write(self.run_root)
        paths.validate_project_write(self.root)
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        checksum = hashlib.sha256(data).hexdigest()
        ref = {"uri": f"urn:sha256:{checksum}", "sha256": checksum}
        path = paths.validate_project_write(self.root / checksum)
        if path.exists():
            self.read(ref)
        else:
            write_bytes(path, data)
        return ref

    def read(self, ref: ArtifactRef):
        if not isinstance(ref, dict) or set(ref) != {"uri", "sha256"}:
            raise ValueError("invalid artifact reference")
        checksum = ref["sha256"]
        if not isinstance(checksum, str) or len(checksum) != 64 or any(char not in "0123456789abcdef" for char in checksum):
            raise ValueError("invalid artifact digest")
        if ref["uri"] != f"urn:sha256:{checksum}":
            raise ValueError("artifact URI must identify its checksum")
        try:
            data = (self.root / checksum).read_bytes()
        except FileNotFoundError:
            if self.root == self.run_root:
                raise
            data = (self.run_root / checksum).read_bytes()
        if hashlib.sha256(data).hexdigest() != checksum:
            raise ValueError("artifact checksum failed")
        return data

    def read_json(self, ref: ArtifactRef):
        return json.loads(self.read(ref))
