"""Versioned messages exchanged by the production workflow."""

from functools import lru_cache
from importlib.resources import files
import json
from typing import TypedDict


class Subject(TypedDict):
    claim_id: str
    contract_sha256: str


class ArtifactRef(TypedDict):
    uri: str
    sha256: str


class Budget(TypedDict):
    wall_seconds: int
    max_attempts: int
    max_model_calls: int


@lru_cache
def _validator(group, name):
    try:
        from jsonschema import Draft202012Validator
    except ImportError as error:
        raise ValueError("Install production dependencies with pip install '.[postgres]'.") from error
    resource = files("research_multiplier").joinpath("schemas", f"{group}.schema.json")
    schema = json.loads(resource.read_text(encoding="utf-8"))
    schema.pop("oneOf")
    schema["$ref"] = f"#/$defs/{name}"
    return Draft202012Validator(schema)


def validate(group, name, value):
    error = next(_validator(group, name).iter_errors(value), None)
    if error:
        path = ".".join(map(str, error.absolute_path))
        location = f" at {path}" if path else ""
        raise ValueError(f"Invalid {name}{location} ({error.validator}).")
