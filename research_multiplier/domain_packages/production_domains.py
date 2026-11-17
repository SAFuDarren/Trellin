"""Registered deterministic production domains and their frozen-contract methods."""

from dataclasses import dataclass
from functools import lru_cache
from importlib.metadata import entry_points
import os
from typing import Callable
from types import ModuleType

from research_multiplier import coloring_claim, domains, flow_certificate, flow_claim, flow_solver, optimization_claim
from research_multiplier.domain_packages import flow_alignment


DEFAULT_DOMAIN = "fixed-directed-max-flow"


@dataclass(frozen=True)
class Domain:
    name: str
    checker_name: str
    checker_version: str
    checker_sources: tuple[ModuleType, ...]
    public_claim: Callable
    validate_claim: Callable
    build_packet: Callable
    validate_packet: Callable
    candidate: Callable
    verify_certificate: Callable
    supplied_candidate: Callable | None = None


def _flow_candidate(spec):
    result = flow_solver.candidate(spec)
    if "observed_value" not in result:
        return result
    value = result["observed_value"]
    return {
        "proof_markdown": (
            f"The exhibited flow and cut have matching value {value}. "
            f"The optimum differs from the frozen target {spec['target_value']}."
        ),
        "basis_items": ["H1", "H2", "H3"],
        "certificate": {"actual_value": value, "flow_cut": result["certificate"]},
        "evidence_kind": "counterexample",
    }


DOMAINS = {
    DEFAULT_DOMAIN: Domain(
        DEFAULT_DOMAIN, "exact-flow-cut", "v0a-2", (flow_claim, flow_certificate),
        flow_claim.public_claim, flow_claim.validate_claim_scope, flow_alignment.build_packet,
        flow_alignment.validate_packet, _flow_candidate, flow_claim.verify_claim_result,
    ),
    coloring_claim.DOMAIN: Domain(
        coloring_claim.DOMAIN, "exact-graph-coloring", "1", (coloring_claim, domains),
        coloring_claim.public_claim, coloring_claim.validate_claim_scope, coloring_claim.build_packet,
        coloring_claim.validate_packet, coloring_claim.candidate, coloring_claim.verify_claim_certificate,
    ),
    **{
        item.domain: Domain(
            item.domain, "exact-" + item.domain, "1", (optimization_claim, domains),
            item.public_claim, item.validate_claim, item.build_packet, item.validate_packet,
            item.candidate, item.verify_certificate, item.supplied,
        )
        for item in (optimization_claim.LP, optimization_claim.SDP)
    },
}


@lru_cache(maxsize=24)
def _installed(name):
    matches = [entry for entry in entry_points(group="research_multiplier.domains") if entry.name == name]
    if len(matches) != 1:
        raise ValueError(f"Expected one enabled installed production domain named {name!r}.")
    factory = matches[0].load()
    supplied = factory()
    if not isinstance(supplied, Domain) or supplied.name != name or not supplied.checker_sources:
        raise ValueError("The installed production domain must register its exact checker sources and identity.")
    return supplied


def get(name):
    if isinstance(name, str):
        if name in DOMAINS:
            return DOMAINS[name]
        enabled = {entry.strip() for entry in os.environ.get("RM_DOMAIN_PACKAGES", "").split(",")}
        if name and name in enabled:
            return _installed(name)
    raise ValueError(f"No deterministic production checker is registered for domain {name!r}.")
