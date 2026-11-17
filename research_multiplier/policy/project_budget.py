"""Project-level monetary reservations shared by structured model adapters."""

from decimal import Decimal
import os

from research_multiplier.storage import paths
from ..models.model_errors import BeforeDispatchUnavailable, ModelFailure
from research_multiplier.storage.postgres import DatabaseOperationError, Store
from research_multiplier.protocol.serialization import fingerprint

_ROLES = ("agent", "scheduler", "verifier", "human")


def route(config):
    """Billing identity stripped of credentials, model input, and response material."""
    return {"provider": config.provider, "model": config.model, "family": config.family,
            "endpoint": config.base_url, "adapter": config.adapter_factory, "binary": config.binary}


def micros(amount):
    if not amount.is_finite() or not 0 <= amount <= 100_000_000:
        raise ValueError("Enter a nonnegative USD amount with at most six decimal places.")
    value = amount * 1_000_000
    if value != value.to_integral_value():
        raise ValueError("Enter a nonnegative USD amount with at most six decimal places.")
    return int(value)


def reserve(config, service, request):
    if paths.database_id(paths.active_workspace()) is None:
        return None
    role = next((name for name in _ROLES if os.environ.get(f"RM_{name.upper()}_DSN")), None)
    if role is None:
        raise ModelFailure("configuration", "Configure a project database login before requesting a model.")
    input_bytes = sum(len(message["content"].encode("utf-8")) for message in request["messages"])
    try:
        with Store() as store:
            return store.query(role, "SELECT rm.reserve_project_model(%s,%s,%s,%s,%s) AS id",
                               fingerprint(route(config)), service, input_bytes, request["budget"]["max_tokens"],
                               request["parameters"]["output_token_limit_enforced"])[0]["id"]
    except DatabaseOperationError as error:
        if error.state == "RMB02":
            raise ModelFailure("project_budget_exhausted", "The monthly project model allowance is exhausted.") from None
        if error.state == "RMB03":
            raise ModelFailure("configuration", str(error)) from None
        if error.retryable:
            raise BeforeDispatchUnavailable(error) from error
        raise


def _usd_fields(row):
    for key, value in list(row.items()):
        if key.endswith("_microusd"):
            row[key.replace("_microusd", "_usd")] = None if value is None else format(Decimal(value) / 1_000_000, ".6f")
    return row


def command(args, store):
    if args.budget_action == "limit":
        row, = store.query("human", "SELECT * FROM rm.set_project_model_limit(%s,%s,%s)",
                           None if args.unlimited else micros(args.usd), args.actor, args.reason)
        return _usd_fields(row)
    if args.budget_action == "tariff":
        from ..model_adapters import ModelConfig

        if args.profile is not None:
            if args.role != "PROVER":
                raise ValueError("Named shadow profiles are available for the PROVER role.")
            from research_multiplier.evaluations.evolution import _profile_config
            config = _profile_config(args.profile)
        else:
            config = ModelConfig.from_env(args.role, private=args.private)
        identity = route(config)
        row, = store.query("human", "SELECT * FROM rm.set_project_model_tariff(%s,%s,%s,%s,%s,%s,%s)",
                           fingerprint(identity), store.json(identity), micros(args.maximum_usd),
                           args.max_input_bytes, args.max_output_tokens, args.actor, args.reason)
        return _usd_fields(row)
    policy = store.query("human", "SELECT * FROM rm.project_model_limits ORDER BY sequence_no DESC LIMIT 1")
    month, = store.query("human", """
        SELECT date_trunc('month', statement_timestamp() AT TIME ZONE 'UTC')::date::text AS period,
           coalesce((SELECT reserved_microusd FROM rm.project_model_months
                     WHERE period = date_trunc('month', statement_timestamp() AT TIME ZONE 'UTC')::date), 0)
           AS reserved_microusd
    """)
    routes = store.query("human", """
        SELECT DISTINCT ON (route_sha256) * FROM rm.project_model_tariffs ORDER BY route_sha256, sequence_no DESC
    """)
    return {"policy": _usd_fields(policy[0]) if policy else None, "month": _usd_fields(month),
            "routes": [_usd_fields(row) for row in routes]}
