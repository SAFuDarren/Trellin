"""Read a selected local Formalpedia export without contacting the platform."""

import hashlib
import heapq
import json
from pathlib import Path, PurePosixPath
import re


_MAX_INDEX_LINE_BYTES = 1 << 20
_MAX_SOURCE_BYTES = 8 << 20
_SHARD = re.compile(r"index-\d+\.jsonl\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_GIT_HASH = re.compile(r"[0-9a-f]{40}\Z")
_ENVIRONMENT = re.compile(r"[0-9a-f]{7,40}\Z")


class FormalpediaError(ValueError):
    """An invalid or incomplete local export."""


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _read_limited(path, limit):
    with path.open("rb") as stream:
        content = stream.read(limit + 1)
    if len(content) > limit:
        raise FormalpediaError(f"Formalpedia file exceeds {limit} bytes: {path.name}")
    return content


def _json_object(raw, label):
    try:
        value = json.loads(raw)
    except (UnicodeError, ValueError) as exc:
        raise FormalpediaError(f"Invalid Formalpedia JSON in {label}") from exc
    if not isinstance(value, dict):
        raise FormalpediaError(f"Expected a JSON object in {label}")
    return value


def _eligible_solutions(entry):
    if entry.get("status") != "Proved":
        return []
    return [solution for solution in entry["solutions"]
            if solution.get("status") == "ACCEPTED" and solution.get("proof_type") == "prove"]


def replay_identity(selected, statement):
    """Identify the local archive contents used to select a replay candidate."""
    entry = selected["entry"]
    if entry != statement["entry"]:
        raise FormalpediaError("The Formalpedia proof and statement belong to different index entries")
    return {
        "snapshot": entry["manifest_sha256"],
        "environment": entry["environment"],
        "theorem": entry["theorem_name"],
        "solution_sha256": selected["solution"]["sha256"],
        "statement_sha256": statement["file_sha256"],
        "index_entry_sha256": entry["index_entry_sha256"],
        "solution_file_sha256": selected["file_sha256"],
    }


class FormalpediaSnapshot:
    """A snapshot on disk. Report content identities, not trust in imported results."""

    def __init__(self, root):
        self.root = Path(root).resolve(strict=True)
        path = self._file(("MANIFEST.json",))
        raw = _read_limited(path, _MAX_INDEX_LINE_BYTES)
        manifest = _json_object(raw, path.name)
        environments = manifest.get("environments")
        if (manifest.get("license") != "Apache-2.0"
                or not isinstance(manifest.get("generated_at"), str)
                or not isinstance(environments, list) or not environments):
            raise FormalpediaError("Expected an Apache-2.0 Formalpedia snapshot manifest")
        self.manifest_sha256 = _digest(raw)
        self.generated_at = manifest["generated_at"]
        self.license = manifest["license"]
        self._environments = {}
        for item in environments:
            if not isinstance(item, dict):
                raise FormalpediaError("Invalid Formalpedia environment metadata")
            name, revision, toolchain = item.get("dir"), item.get("mathlib_rev"), item.get("toolchain")
            if (not isinstance(name, str) or not _ENVIRONMENT.fullmatch(name)
                    or not isinstance(revision, str) or not _GIT_HASH.fullmatch(revision)
                    or not revision.startswith(name) or name in self._environments
                    or not isinstance(toolchain, str) or not toolchain.startswith("leanprover/lean4:")):
                raise FormalpediaError("Invalid Formalpedia environment, revision, or toolchain")
            self._environments[name] = dict(item)

    def environments(self):
        return [dict(item) for item in self._environments.values()]

    def _environment(self, name):
        try:
            return self._environments[name]
        except (KeyError, TypeError) as exc:
            raise FormalpediaError(f"Unknown Formalpedia environment: {name}") from exc

    def _file(self, parts):
        path = self.root.joinpath(*parts).resolve(strict=True)
        if not path.is_relative_to(self.root) or not path.is_file():
            raise FormalpediaError("Formalpedia path does not name a file inside the snapshot")
        return path

    def _source_file(self, environment, directory, relative):
        if not isinstance(relative, str) or "\\" in relative:
            raise FormalpediaError("Invalid Formalpedia source path")
        parts = PurePosixPath(relative).parts
        if len(parts) != 4 or parts[:3] != ("envs", environment, directory) or not parts[-1].endswith(".lean"):
            raise FormalpediaError(f"Formalpedia source must be in envs/{environment}/{directory}")
        path = self._file(parts)
        if path.relative_to(self.root).parts[:3] != parts[:3]:
            raise FormalpediaError("Formalpedia source resolves outside its declared environment")
        return path

    def _index_shards(self, environment):
        directory = self.root / "envs" / environment
        return sorted(path.name for path in directory.glob("index-*.jsonl") if _SHARD.fullmatch(path.name))

    def _index_rows(self, environment, *, shards=None):
        if shards is None:
            shards = self._index_shards(environment)
        if not shards:
            raise FormalpediaError(f"No local index shards for Formalpedia environment {environment}")
        for shard in shards:
            path = self._file(("envs", environment, shard))
            if path.relative_to(self.root).parts[:2] != ("envs", environment):
                raise FormalpediaError("Formalpedia index resolves outside its declared environment")
            with path.open("rb") as stream:
                line_number = 0
                while raw := stream.readline(_MAX_INDEX_LINE_BYTES + 1):
                    line_number += 1
                    label = f"{environment}/{shard}:{line_number}"
                    if len(raw) > _MAX_INDEX_LINE_BYTES:
                        raise FormalpediaError(f"Formalpedia index row too large in {label}")
                    if raw.strip():
                        yield label, raw

    def _record(self, label, raw, environment):
        record = _json_object(raw, label)
        if (record.get("env") != environment or not isinstance(record.get("id"), str)
                or not isinstance(record.get("theorem_name"), str) or not record["theorem_name"]):
            raise FormalpediaError(f"Invalid theorem identity in Formalpedia index {label}")
        solutions = record.get("solutions")
        if not isinstance(solutions, list) or any(not isinstance(item, dict) for item in solutions):
            raise FormalpediaError(f"Invalid solution metadata in Formalpedia index {label}")
        return record

    def _annotate(self, record, environment, raw):
        metadata = self._environment(environment)
        return {
            **record,
            "environment": environment,
            "toolchain": metadata["toolchain"],
            "mathlib_rev": metadata["mathlib_rev"],
            "snapshot_generated_at": self.generated_at,
            "manifest_sha256": self.manifest_sha256,
            "index_entry_sha256": _digest(raw),
            "license": self.license,
            "replay_candidate": bool(_eligible_solutions(record)),
        }

    def search(self, query, *, environment=None, limit=20, proved_only=False):
        """Match all query words in the name or title, prioritizing exact names."""
        if not isinstance(query, str) or not query.strip() or len(query) > 512:
            raise FormalpediaError("Enter a Formalpedia search query of 1 to 512 characters")
        if type(limit) is not int or not 1 <= limit <= 200:
            raise FormalpediaError("Formalpedia search limit must be between 1 and 200")
        query = query.strip().casefold()
        words = query.split()
        if environment is not None:
            name = self._environment(environment)["dir"]
            indexes = {name: self._index_shards(name)}
        else:
            indexes = {name: shards for name in self._environments if (shards := self._index_shards(name))}
            if not indexes:
                raise FormalpediaError("No local index shards for any environment in this Formalpedia snapshot")

        def matches():
            for name, shards in indexes.items():
                for label, raw in self._index_rows(name, shards=shards):
                    record = self._record(label, raw, name)
                    theorem = record["theorem_name"].casefold()
                    title = str(record.get("title") or "").casefold()
                    if not all(word in theorem or word in title for word in words):
                        continue
                    if proved_only and not _eligible_solutions(record):
                        continue
                    rank = (theorem != query, not theorem.startswith(query), query not in theorem,
                            query not in title, theorem, name, record["id"])
                    yield rank, record, name, raw

        top = heapq.nsmallest(limit, matches(), key=lambda item: item[0])
        return [self._annotate(record, name, raw) for _, record, name, raw in top]

    def _entries(self, environment, identifiers):
        self._environment(environment)
        if any(not isinstance(value, str) or not value or len(value) > 512 for value in identifiers):
            raise FormalpediaError("Enter an exact Formalpedia theorem name, module name, or ID")
        requested = set(identifiers)
        if not requested:
            return {}
        quoted = {json.dumps(value, ensure_ascii=ascii_only).encode("utf-8")
                  for value in requested for ascii_only in (False, True)}
        # Decode just the potential matches. The exporter uses JSON strings to
        # store the exact identifiers, even when a title contains the same text.
        pattern = re.compile(b"|".join(re.escape(value) for value in sorted(quoted)))
        found = {}
        for label, raw in self._index_rows(environment):
            if not pattern.search(raw):
                continue
            record = self._record(label, raw, environment)
            matches = requested.intersection(value for value in (record["id"], record["theorem_name"], record.get("module"))
                                             if isinstance(value, str))
            if matches:
                entry = self._annotate(record, environment, raw)
                for identifier in matches:
                    if identifier in found:
                        raise FormalpediaError(f"Ambiguous Formalpedia theorem identity: {identifier}")
                    found[identifier] = entry
        missing = next((value for value in identifiers if value not in found), None)
        if missing is not None:
            raise FormalpediaError(f"No Formalpedia theorem {missing} in environment {environment}")
        return found

    def entry(self, environment, identifier):
        """Select an exact theorem name, module name, or ID within one environment."""
        return self._entries(environment, (identifier,))[identifier]

    def solution(self, environment, identifier, *, solution_id=None):
        """Load an accepted proof body and check the digest exported by the platform."""
        return self._solution(self.entry(environment, identifier), solution_id)

    def replay_sources(self, environment, selections):
        """Read ordered (identifier, solution ID) pairs in one index pass."""
        selections = tuple(selections)
        entries = self._entries(environment, tuple(identifier for identifier, _ in selections))
        return [(self._solution(entries[identifier], solution_id), self._statement(entries[identifier]))
                for identifier, solution_id in selections]

    def _solution(self, entry, solution_id):
        candidates = _eligible_solutions(entry)
        if solution_id is not None:
            candidates = [candidate for candidate in candidates if candidate.get("id") == solution_id]
        if not candidates:
            raise FormalpediaError(f"No fully accepted Formalpedia proof for {entry['theorem_name']}")
        solution = candidates[0]
        digest = solution.get("sha256")
        if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
            raise FormalpediaError("Missing or invalid Formalpedia solution SHA-256")
        path = self._source_file(entry["environment"], "Solutions", solution.get("path"))
        raw = _read_limited(path, _MAX_SOURCE_BYTES)
        header, separator, body = raw.partition(b"\n\n")
        if (not separator or not header.startswith(b"-- Prove2me | solution ")
                or _digest(body) != digest):
            raise FormalpediaError(f"Formalpedia proof does not match its exported SHA-256: {path.name}")
        try:
            return {"entry": entry, "solution": dict(solution), "body": body.decode("utf-8"),
                    "file_sha256": _digest(raw)}
        except UnicodeError as exc:
            raise FormalpediaError(f"Formalpedia proof is not UTF-8: {path.name}") from exc

    def statement(self, environment, identifier):
        """Load the upstream statement for comparison, including its placeholder."""
        return self._statement(self.entry(environment, identifier))

    def _statement(self, entry):
        path = self._source_file(entry["environment"], "Theorems", entry.get("statement_path"))
        raw = _read_limited(path, _MAX_SOURCE_BYTES)
        try:
            return {"entry": entry, "text": raw.decode("utf-8"), "file_sha256": _digest(raw)}
        except UnicodeError as exc:
            raise FormalpediaError(f"Formalpedia statement is not UTF-8: {path.name}") from exc
