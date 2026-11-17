"""Copy and verify a snapshot of the shared artifact store."""

import json
from pathlib import Path

from research_multiplier.storage import paths
from .artifacts import Artifacts, write_bytes, write_json


def workspace():
    return Artifacts(paths.object_store(), workspace=False)


def _is_digest(name):
    return len(name) == 64 and all(char in "0123456789abcdef" for char in name)


def _ref(checksum):
    return {"uri": f"urn:sha256:{checksum}", "sha256": checksum}


def backup(destination):
    source = workspace()
    target = paths.external_destination(destination, "backups")
    if not source.root.is_dir():
        raise ValueError("The configured artifact store does not exist.")
    target.mkdir(mode=0o700, parents=True, exist_ok=False)
    content = target / "objects"
    content.mkdir(mode=0o700)
    names = sorted(path.name for path in source.root.iterdir() if _is_digest(path.name))
    for checksum in names:
        write_bytes(content / checksum, source.read(_ref(checksum)), external=True)
    workspace_id = paths.workspace_id(paths.active_workspace())
    manifest = {"format_version": 1, "objects": names}
    if workspace_id is not None:
        manifest["workspace_id"] = str(workspace_id)
    write_json(target / "manifest.json", manifest, external=True)
    return {"directory": str(target), "objects": len(names)}


def restore(source):
    source = Path(source)
    target = workspace()
    archive = Artifacts(source / "objects", workspace=False)
    manifest = json.loads((source / "manifest.json").read_bytes())
    if not isinstance(manifest, dict) or manifest.get("format_version") != 1:
        raise ValueError("Unsupported artifact backup manifest.")
    if paths.strict_workspace():
        selected = paths.require_workspace()
        owner = manifest.get("workspace_id")
        current = paths.workspace_id(selected)
        if ((owner is not None and (current is None or owner != str(current)))
                or (owner is None and not source.resolve().is_relative_to(selected / "backups"))):
            raise ValueError("Use an artifact backup from this research workspace or restore a paired project archive.")
    names = manifest.get("objects")
    if not isinstance(names, list) or any(not isinstance(name, str) or not _is_digest(name) for name in names):
        raise ValueError("The backup contains invalid artifact references.")
    if len(set(names)) != len(names):
        raise ValueError("The backup lists duplicate artifacts.")
    # Verify the entire snapshot before publishing missing objects.
    for checksum in names:
        archive.read(_ref(checksum))
        if (target.root / checksum).exists():
            target.read(_ref(checksum))
    paths.validate_project_write(target.root).mkdir(mode=0o700, parents=True, exist_ok=True)
    for checksum in names:
        if not (target.root / checksum).exists():
            data = archive.read(_ref(checksum))
            write_bytes(target.root / checksum, data)
    return {"directory": str(target.root), "objects": len(names)}
