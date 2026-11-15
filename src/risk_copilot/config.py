"""Layered configuration loader.

Resolution order, lowest precedence first::

    conf/base.yml                     topology defaults
    conf/profiles/<profile>.yml       environment overlay (deep-merged)
    conf/<domain>.yml                 generation / detection / models / external / ml
    <domain>_overrides in the profile  profile-specific domain tweaks
    environment variables (RC_*)      developer escape hatch
    explicit CLI flags                highest precedence

Two rules make this worth having rather than decoration:

1. **Nothing is hardcoded.** No database, schema, warehouse, role or threshold
   literal appears in Python or SQL. SQL files are Jinja templates rendered
   with :meth:`Settings.sql_context`.
2. **Business thresholds are not config at runtime.** ``conf/detection.yml`` is
   a *seed* for ``GOVERNANCE.POLICY_CLAUSE``; detection SQL joins to that table.
   This module loads the seed so it can be deployed and validated, and
   deliberately offers no API for reading a threshold at detection time.
"""

from __future__ import annotations

import copy
import datetime as dt
import os
import re
from functools import cached_property
from pathlib import Path
from typing import Any

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from risk_copilot import ids

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

ENV_PREFIX = "RC_"
DEFAULT_PROFILE = "dev"
VALID_PROFILES = ("dev", "demo")

#: Snowflake unquoted identifiers: letter/underscore start, then alphanumerics
#: and underscores. We reject anything else outright rather than quoting it,
#: because a config-driven deployer that accepts arbitrary identifiers is an
#: injection vector: these values are interpolated into DDL.
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")

#: ``TARGET_LAG`` accepts either a duration or the literal ``DOWNSTREAM``.
_TARGET_LAG_RE = re.compile(r"^(DOWNSTREAM|\d+\s+(second|minute|hour|day)s?)$", re.IGNORECASE)
_DURATION_RE = re.compile(r"^(\d+)\s+(second|minute|hour|day)s?$", re.IGNORECASE)
_LAG_UNIT_SECONDS = {"second": 1, "minute": 60, "hour": 3600, "day": 86400}

_DOMAIN_FILES = {
    "generation": "generation.yml",
    "detection": "detection.yml",
    "models": "models.yml",
    "external": "external.yml",
    "ml": "ml.yml",
}

#: Probe that establishes whether extended Time Travel is allowed at all. The
#: probe asks for 14 days because that is what ``conf/profiles/demo.yml``
#: requests; a failure is the account refusing extended retention, not a
#: statement about 14 specifically.
_RETENTION_PROBE_ID = "RETENTION_14_DAYS"

#: Snowflake names the actual cap in the rejection: "Exceeds maximum allowable
#: retention time (1 day(s))". Parsing it is preferred over inferring a cap,
#: because it is the account stating its own limit.
_RETENTION_CAP_RE = re.compile(r"maximum allowable retention time\s*\((\d+)\s*day", re.IGNORECASE)


class ConfigError(RuntimeError):
    """Raised when configuration is missing, malformed or internally inconsistent.

    Always raised eagerly at load time. A misconfigured deploy must fail before
    it touches Snowflake, not halfway through creating objects.
    """


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge ``overlay`` over ``base``, returning a new dict.

    Dicts merge key-wise; every other type (including lists) is replaced
    wholesale. Replacing lists is deliberate -- a profile that narrows
    ``branch_regions`` must be able to shorten the list, not append to it.
    """
    result = copy.deepcopy(base)
    for key, value in overlay.items():
        existing = result.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            result[key] = deep_merge(existing, value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ConfigError(f"Configuration file not found: {path}")
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:  # pragma: no cover - surfaced verbatim
        raise ConfigError(f"Malformed YAML in {path}: {exc}") from exc
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise ConfigError(f"Expected a mapping at the top level of {path}")
    return loaded


def validate_identifier(value: str, *, what: str) -> str:
    """Return ``value`` upper-cased, or raise if it is not a safe identifier.

    Guards the DDL-rendering path: these strings are interpolated into SQL, so
    an unvalidated value from a YAML file would be an injection point.
    """
    if not isinstance(value, str) or not _IDENTIFIER_RE.match(value):
        raise ConfigError(
            f"{what} must be a valid unquoted Snowflake identifier "
            f"(letters, digits, underscore; not starting with a digit), got {value!r}"
        )
    return value.upper()


def _env_override(key: str) -> str | None:
    raw = os.environ.get(f"{ENV_PREFIX}{key}")
    if raw is None:
        return None
    stripped = raw.strip()
    return stripped or None


def capabilities_file(repo_root: Path, connection: str) -> Path:
    """Where ``scripts/probe_capabilities.py`` writes evidence for ``connection``.

    Lives here rather than in :mod:`risk_copilot.capabilities` so that both this
    module and that one resolve the same path without a circular import:
    ``capabilities`` already depends on ``config``.
    """
    return repo_root / "conf" / "generated" / f"capabilities.{connection}.yml"


def data_manifest_file(repo_root: Path) -> Path:
    """The source dataset registry. ``doctor`` and ``generate`` both read it."""
    return repo_root / "data" / "MANIFEST.yml"


def _retention_ceiling(repo_root: Path, connection: str) -> tuple[int, dict[str, str]] | None:
    """Return the account's Time Travel cap in days, with the evidence for it.

    Returns ``None`` when nothing can be asserted -- no evidence file, the probe
    was never run, the probe passed, or the cap could not be established. In
    every one of those cases the configured value is left untouched: inventing a
    ceiling would be exactly the kind of unmeasured assumption this project
    exists to remove, and an unclamped deploy fails loudly with the account's own
    error rather than quietly deploying something different from what was asked.

    The cap is read from the rejection message first, because that is the account
    stating its own limit. ``param_data_retention_time_in_days`` is the fallback:
    on an edition that caps retention, the account default and the cap coincide.
    """
    path = capabilities_file(repo_root, connection)
    if not path.is_file():
        return None

    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return None
    if not isinstance(payload, dict):
        return None

    probes = payload.get("probes")
    entry = probes.get(_RETENTION_PROBE_ID) if isinstance(probes, dict) else None
    if not isinstance(entry, dict) or entry.get("available"):
        return None

    evidence = {
        "probe_id": _RETENTION_PROBE_ID,
        "failure_class": str(entry.get("failure_class") or "unclassified"),
        "error_code": str(entry.get("error_code") or ""),
        "evidence_path": str(path),
    }

    if match := _RETENTION_CAP_RE.search(str(entry.get("error") or "")):
        return int(match.group(1)), evidence | {"cap_source": "probe error message"}

    account = payload.get("account")
    raw = account.get("param_data_retention_time_in_days") if isinstance(account, dict) else None
    try:
        return int(str(raw)), evidence | {"cap_source": "account DATA_RETENTION_TIME_IN_DAYS"}
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# Typed sections
# --------------------------------------------------------------------------- #


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Schemas(_Frozen):
    """The six logical schemas, plus ``ML``.

    ``ground_truth`` is isolated by design: it holds the labels, and the
    label-leakage gate asserts that nothing in it is reachable from the
    semantic view.
    """

    raw: str
    curated: str
    governance: str
    evidence: str
    audit: str
    ground_truth: str
    ml: str

    @field_validator("*")
    @classmethod
    def _identifier(cls, v: str) -> str:
        return validate_identifier(v, what="Schema name")


class Warehouse(_Frozen):
    name: str
    size: str
    auto_suspend_secs: int = Field(ge=30, le=3600)
    purpose: str = ""

    @field_validator("name")
    @classmethod
    def _identifier(cls, v: str) -> str:
        return validate_identifier(v, what="Warehouse name")

    @field_validator("size")
    @classmethod
    def _size(cls, v: str) -> str:
        allowed = {
            "XSMALL",
            "SMALL",
            "MEDIUM",
            "LARGE",
            "XLARGE",
            "XXLARGE",
            "XXXLARGE",
            "X4LARGE",
        }
        upper = v.upper().replace("-", "").replace("_", "")
        if upper not in allowed:
            raise ConfigError(
                f"Unsupported warehouse size {v!r}; expected one of {sorted(allowed)}"
            )
        return upper


class Roles(_Frozen):
    admin: str
    service: str
    compliance_officer: str
    branch_analysts: dict[str, str]
    #: The hosted (Streamlit Community Cloud) app's role: read the demo, run the case tools.
    public_app: str = ""
    #: The hosted app's Principal Officer: the public role plus the compliance
    #: role, used only while the officer persona is signed in. Empty for none.
    public_officer: str = ""

    @field_validator("admin", "service", "compliance_officer")
    @classmethod
    def _identifier(cls, v: str) -> str:
        return validate_identifier(v, what="Role name")

    @field_validator("public_app", "public_officer")
    @classmethod
    def _optional_identifier(cls, v: str) -> str:
        return validate_identifier(v, what="Role name") if v else ""

    @field_validator("branch_analysts")
    @classmethod
    def _analysts(cls, v: dict[str, str]) -> dict[str, str]:
        if not v:
            raise ConfigError(
                "At least one branch analyst role is required: the row-access-policy "
                "demo ('same question, two roles, two answers') depends on it."
            )
        return {
            validate_identifier(region, what="Branch region"): validate_identifier(
                role, what="Branch analyst role"
            )
            for region, role in v.items()
        }


class Stages(_Frozen):
    data: str
    policy_docs: str
    generated: str
    scripts: str
    #: In the GROUND_TRUTH schema, granted to no role (obligation 4).
    ground_truth: str

    @field_validator("*")
    @classmethod
    def _identifier(cls, v: str) -> str:
        return validate_identifier(v, what="Stage name")


class Retention(_Frozen):
    """Time Travel retention, in days.

    Correctness does not depend on these values: ``EVIDENCE_QUERY_LOG``
    materialises a result snapshot, which is what the Reviewer compares
    against. Retention is what makes the *provenance* claim demonstrable, and
    the default of one day would silently expire ``as_of_ts`` mid-demo.

    A profile states what it *wants*. What the account *allows* is a separate
    question, answered by recorded probe evidence and applied by
    :func:`_retention_ceiling`, so a profile never has to be rewritten to suit
    one account. See :class:`RetentionClamp`.
    """

    landing_days: int = Field(ge=0, le=90)
    evidence_days: int = Field(ge=0, le=90)
    default_days: int = Field(ge=0, le=90)


class RetentionClamp(_Frozen):
    """Record of retention having been reduced to what the account permits.

    Exists so the reduction is *reported* rather than silent. ``demo.yml`` asks
    for 14 days; a Standard-edition account rejects anything above its cap at
    ``CREATE SCHEMA`` time with error 001048, which would fail the deploy. The
    clamp keeps the deploy working while making the lost capability visible --
    the alternative, editing the profile down to 1, would hide a real constraint
    and would be wrong the moment the account is upgraded.
    """

    requested: dict[str, int]
    applied: dict[str, int]
    max_days: int
    probe_id: str
    failure_class: str
    error_code: str
    cap_source: str
    evidence_path: str

    def summary(self) -> str:
        """One line naming what was reduced and on what evidence."""
        reduced = ", ".join(
            f"{key} {self.requested[key]}->{self.applied[key]}"
            for key in sorted(self.requested)
            if self.requested[key] != self.applied[key]
        )
        code = f" {self.error_code}" if self.error_code else ""
        return (
            f"{reduced}; account cap {self.max_days} day(s) per {self.cap_source} "
            f"[{self.probe_id}: {self.failure_class}{code}]"
        )


class DynamicTableSpec(_Frozen):
    name: str
    target_lag: str
    #: Config keys of the dynamic tables this one reads. See ``_check_lag_graph``.
    upstream: tuple[str, ...] = ()

    @field_validator("name")
    @classmethod
    def _identifier(cls, v: str) -> str:
        return validate_identifier(v, what="Dynamic table name")

    @field_validator("target_lag")
    @classmethod
    def _lag(cls, v: str) -> str:
        if not _TARGET_LAG_RE.match(v.strip()):
            raise ConfigError(
                f"Invalid TARGET_LAG {v!r}; expected 'DOWNSTREAM' or "
                "'<n> second|minute|hour|day(s)'"
            )
        return v.strip().upper() if v.strip().upper() == "DOWNSTREAM" else v.strip().lower()

    @property
    def lag_seconds(self) -> int | None:
        """The lag in seconds, or ``None`` for ``DOWNSTREAM``."""
        if self.target_lag == "DOWNSTREAM":
            return None
        return duration_seconds(self.target_lag)


def duration_seconds(text: str) -> int:
    """``'<n> second|minute|hour|day(s)'`` in seconds, the grammar ``TARGET_LAG`` uses."""
    match = _DURATION_RE.match(str(text).strip())
    if not match:
        raise ConfigError(f"Invalid duration {text!r}; expected '<n> second|minute|hour|day(s)'")
    return int(match.group(1)) * _LAG_UNIT_SECONDS[match.group(2).lower()]


def _check_lag_graph(specs: dict[str, DynamicTableSpec]) -> None:
    """Reject a dynamic-table lag graph Snowflake would refuse or never refresh.

    Two rules, both from Snowflake's scheduler: a table cannot be fresher than
    a table it reads (CREATE fails), and a ``DOWNSTREAM`` table that no other
    dynamic table reads never refreshes (silently -- a view as its only consumer
    does not count). A ``DOWNSTREAM`` table refreshes as often as its most
    demanding consumer, so that is the lag it is compared at.
    """
    consumers: dict[str, list[str]] = {key: [] for key in specs}
    for key, spec in specs.items():
        for up in spec.upstream:
            if up not in specs:
                raise ConfigError(f"dynamic_tables.{key}.upstream names unknown table {up!r}")
            if up == key:
                raise ConfigError(f"dynamic_tables.{key} lists itself as upstream")
            consumers[up].append(key)

    def visit(key: str, path: tuple[str, ...]) -> None:
        if key in path:
            raise ConfigError(f"dynamic_tables: upstream cycle {' -> '.join((*path, key))}")
        for up in specs[key].upstream:
            visit(up, (*path, key))

    for key in specs:
        visit(key, ())

    def effective(key: str) -> int:
        own = specs[key].lag_seconds
        if own is not None:
            return own
        if not consumers[key]:
            raise ConfigError(
                f"dynamic_tables.{key} is DOWNSTREAM but no dynamic table lists it as "
                "upstream, so it would never refresh. Give it a lag."
            )
        return min(effective(c) for c in consumers[key])

    for key, spec in specs.items():
        lag = effective(key)
        for up in spec.upstream:
            up_lag = specs[up].lag_seconds
            if up_lag is not None and lag < up_lag:
                raise ConfigError(
                    f"dynamic_tables.{key} (lag {spec.target_lag}) reads {up} (lag "
                    f"{specs[up].target_lag}): a dynamic table cannot be fresher than its "
                    "upstream, and Snowflake rejects the CREATE."
                )


class StreamsTasks(_Frozen):
    """Signal routing: the stream on DT_SIGNALS_FAST, the router and its task.

    The task is triggered (``WHEN SYSTEM$STREAM_HAS_DATA``, no schedule, D38),
    so there is no interval to configure. ``resume_route_task`` decides whether
    the deploy leaves it running: a resumed task spends warehouse credits every
    time a refresh changes a signal, which dev does not need.
    """

    signals_stream: str
    route_signals_procedure: str
    route_signals_task: str
    resume_route_task: bool

    @field_validator("signals_stream", "route_signals_procedure", "route_signals_task")
    @classmethod
    def _identifier(cls, v: str) -> str:
        return validate_identifier(v, what="Stream/task object name")


class ReplaySpec(_Frozen):
    """``conf/generation.yml`` ``replay``: the hold-back and the replay pacing.

    ``hold_back_from`` null means ``load`` loads everything and there is nothing
    to replay. ``injections`` maps a ``replay --inject`` name to an
    ``injection`` cohort key; the cohort's ``label`` selects its pending legs.
    """

    hold_back_from: dt.datetime | None
    sim_step: str
    sleep_seconds: float
    batch_legs: int
    injections: dict[str, str]

    @field_validator("hold_back_from", mode="before")
    @classmethod
    def _moment(cls, v: Any) -> Any:
        # YAML reads `2022-09-03` as a date: the hold-back starts at its midnight.
        if isinstance(v, dt.date) and not isinstance(v, dt.datetime):
            return dt.datetime(v.year, v.month, v.day)
        return v

    @field_validator("hold_back_from")
    @classmethod
    def _naive(cls, v: dt.datetime | None) -> dt.datetime | None:
        if v is not None and v.tzinfo is not None:
            raise ConfigError("replay.hold_back_from must be a naive timestamp (TIMESTAMP_NTZ)")
        return v

    @field_validator("sim_step")
    @classmethod
    def _step(cls, v: str) -> str:
        if duration_seconds(v) <= 0:
            raise ConfigError("replay.sim_step must be positive")
        return v.strip().lower()

    @field_validator("sleep_seconds")
    @classmethod
    def _sleep(cls, v: float) -> float:
        if v < 0:
            raise ConfigError("replay.sleep_seconds must be >= 0")
        return v

    @field_validator("batch_legs")
    @classmethod
    def _batch(cls, v: int) -> int:
        if v < 1:
            raise ConfigError("replay.batch_legs must be >= 1")
        return v

    @property
    def sim_step_seconds(self) -> int:
        return duration_seconds(self.sim_step)


def _replay_spec(generation: dict[str, Any]) -> ReplaySpec:
    raw = generation.get("replay")
    if not isinstance(raw, dict):
        raise ConfigError("conf/generation.yml has no `replay` block")
    try:
        spec = ReplaySpec(**raw)
    except ValidationError as exc:
        raise ConfigError(f"conf/generation.yml `replay`: {exc}") from exc
    cohorts = generation.get("injection") or {}
    for name, key in spec.injections.items():
        cohort = cohorts.get(key)
        if not isinstance(cohort, dict) or not cohort.get("label"):
            raise ConfigError(
                f"replay.injections.{name} names injection cohort {key!r}, which "
                "conf/generation.yml `injection` does not define with a label"
            )
    return spec


#: CASE_RECORD.case_type values a single rule can map to; CROSS_DOMAIN is derived.
CASE_TYPES = ("FRAUD", "AML")


def _check_routing(detection: dict[str, Any]) -> None:
    """Refuse a routing block ROUTE_SIGNALS could not apply as written."""
    routing = detection.get("routing") or {}
    order = [str(s).upper() for s in routing.get("severity_order") or []]
    if not order or len(set(order)) != len(order):
        raise ConfigError(
            "conf/detection.yml: routing.severity_order must list distinct severities"
        )
    if str(routing.get("min_severity_for_case") or "").upper() not in order:
        raise ConfigError(
            "conf/detection.yml: routing.min_severity_for_case must be one of "
            f"routing.severity_order {order}"
        )
    if not routing.get("open_case_statuses"):
        raise ConfigError("conf/detection.yml: routing.open_case_statuses is empty")
    minimum = routing.get("cross_domain_min_distinct_rules")
    if isinstance(minimum, bool) or not isinstance(minimum, int) or minimum < 2:
        raise ConfigError(
            "conf/detection.yml: routing.cross_domain_min_distinct_rules must be an integer >= 2"
        )
    case_types = routing.get("case_type_by_rule") or {}
    for rule in detection.get("rules") or []:
        value = str(case_types.get(rule.get("rule_id")) or "").upper()
        if value not in CASE_TYPES:
            raise ConfigError(
                f"conf/detection.yml: routing.case_type_by_rule must give rule "
                f"{rule.get('rule_id')!r} one of {list(CASE_TYPES)}"
            )


class CortexObjects(_Frozen):
    search_service: str
    search_source_table: str
    search_target_lag: str
    semantic_view: str
    agent: str
    #: Where the STR agent's tools live. Shared by every profile, so not suffixed.
    agent_database: str = ""
    agent_schema: str = ""
    #: GENERATE_AI_STR spend cap (the button is on a public URL).
    ai_str_cooldown_minutes: int = Field(default=5, ge=0)
    ai_str_daily_runs: int = Field(default=30, ge=1)
    #: Printed on the STR PDF.
    reporting_bank: str = "Demo Bank Ltd."
    principal_officer: str = "Rajesh Menon"

    @field_validator("search_service", "search_source_table", "semantic_view", "agent")
    @classmethod
    def _identifier(cls, v: str) -> str:
        return validate_identifier(v, what="Cortex object name")

    @field_validator("agent_database", "agent_schema")
    @classmethod
    def _optional_identifier(cls, v: str) -> str:
        return validate_identifier(v, what="Agent object name") if v else ""


_RUNTIME_RE = re.compile(r"^SYSTEM\$[A-Z0-9_]+$")
#: case_tools keys that hold data values rather than object names.
_CASE_TOOL_DATA = frozenset(
    {"str_template_id", "sla_clause_id", "attestation_clause_id", "narrative_author"}
)


class AppSpec(_Frozen):
    """The Streamlit-in-Snowflake app (``conf/base.yml:app``).

    ``schema`` and ``warehouse`` are config *keys*, not names, so a schema or
    warehouse rename is still one line; Settings checks they resolve.
    """

    name: str
    title: str
    schema_key: str = Field(alias="schema")
    stage: str
    warehouse: str
    runtime_name: str
    #: Empty: the account's DEFAULT_STREAMLIT_COMPUTE_POOL, read at deploy time.
    compute_pool: str = ""
    #: Fully qualified artifact repository for the app's packages; empty for none.
    artifact_repository: str = ""
    #: Service user the hosted app connects as (key pair only); empty for none.
    public_user: str = ""
    #: Service user the hosted app signs STRs as (roles.public_officer); empty for none.
    public_officer_user: str = ""

    @field_validator("name", "stage")
    @classmethod
    def _identifier(cls, v: str) -> str:
        return validate_identifier(v, what="App object name")

    @field_validator("public_user", "public_officer_user")
    @classmethod
    def _user(cls, v: str) -> str:
        return validate_identifier(v, what="App public user") if v else ""

    @field_validator("schema_key")
    @classmethod
    def _schema(cls, v: str) -> str:
        if v not in Schemas.model_fields:
            raise ConfigError(f"app.schema must be one of {list(Schemas.model_fields)}, got {v!r}")
        return v

    @field_validator("runtime_name")
    @classmethod
    def _runtime(cls, v: str) -> str:
        if not _RUNTIME_RE.match(v):
            raise ConfigError(f"app.runtime_name must look like SYSTEM$..., got {v!r}")
        return v

    @field_validator("compute_pool")
    @classmethod
    def _pool(cls, v: str) -> str:
        return validate_identifier(v, what="Compute pool") if v else ""

    @field_validator("artifact_repository")
    @classmethod
    def _repository(cls, v: str) -> str:
        if not v:
            return ""
        parts = v.split(".")
        if len(parts) != 3:
            raise ConfigError(f"app.artifact_repository must be DB.SCHEMA.NAME, got {v!r}")
        return ".".join(validate_identifier(p, what="Artifact repository") for p in parts)

    @field_validator("title")
    @classmethod
    def _title(cls, v: str) -> str:
        # Rendered into a SQL string literal; refuse rather than escape.
        if not v or "'" in v or "\\" in v:
            raise ConfigError(f"app.title must be non-empty without quotes or backslashes: {v!r}")
        return v


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #


class Settings(BaseModel):
    """Fully resolved, immutable configuration for one profile.

    Build with :meth:`load`. Direct construction is supported for tests but
    bypasses the layering.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    repo_root: Path
    profile: str
    object_suffix: str

    database: str
    schemas: Schemas
    warehouses: dict[str, Warehouse]
    roles: Roles
    stages: Stages
    retention: Retention
    #: Set when the account permits less Time Travel than the profile requested.
    #: ``None`` means the profile's values were applied as written.
    retention_clamp: RetentionClamp | None = None
    dynamic_tables: dict[str, DynamicTableSpec]
    views: dict[str, str]
    streams_tasks: StreamsTasks
    cortex: CortexObjects
    app: AppSpec
    case_tools: dict[str, str] = Field(default_factory=dict)
    # Domain configs are kept as plain mappings: they are large, mostly
    # data-shaped, and consumed by the generators that own their semantics.
    # Validating them here would duplicate that ownership.
    generation: dict[str, Any] = Field(default_factory=dict)
    detection: dict[str, Any] = Field(default_factory=dict)
    models: dict[str, Any] = Field(default_factory=dict)
    external: dict[str, Any] = Field(default_factory=dict)
    ml: dict[str, Any] = Field(default_factory=dict)

    # ------------------------------------------------------------------ #
    # Cross-section consistency
    # ------------------------------------------------------------------ #

    @model_validator(mode="after")
    def _check_consistency(self) -> Settings:
        for required in ("dev", "pipeline", "demo"):
            if required not in self.warehouses:
                raise ConfigError(f"Missing required warehouse definition: {required!r}")

        _check_lag_graph(self.dynamic_tables)

        if self.app.warehouse not in self.warehouses:
            raise ConfigError(
                f"app.warehouse {self.app.warehouse!r} is not a key of warehouses: "
                f"{sorted(self.warehouses)}"
            )

        # A branch analyst role whose region is never generated silently makes
        # the RLS demo return an empty result instead of a filtered one -- a
        # failure that looks like working access control.
        configured = set(self.roles.branch_analysts)
        generated = {
            str(entry["code"]).upper()
            for entry in self.generation.get("entities", {}).get("branch_regions", [])
            if isinstance(entry, dict) and "code" in entry
        }
        if generated and not configured <= generated:
            missing = sorted(configured - generated)
            raise ConfigError(
                f"Branch analyst roles exist for regions that generation never "
                f"produces: {missing}. The row-access-policy demo would show an "
                f"empty result rather than a filtered one. Reconcile "
                f"conf/base.yml:roles.branch_analysts with "
                f"conf/generation.yml:entities.branch_regions."
            )

        # Severity keys must be known, or ROUTE_SIGNALS cannot rank.
        order = {s.upper() for s in self.detection.get("routing", {}).get("severity_order", [])}
        if order:
            unknown = {
                sev.upper()
                for sev in self.detection.get("severity", {}).values()
                if sev.upper() not in order
            }
            if unknown:
                raise ConfigError(
                    f"conf/detection.yml assigns severities not present in "
                    f"routing.severity_order: {sorted(unknown)}"
                )
        if self.detection.get("rules"):
            _check_routing(self.detection)
        if "replay" in self.generation:
            _replay_spec(self.generation)
        return self

    # ------------------------------------------------------------------ #
    # Loading
    # ------------------------------------------------------------------ #

    @classmethod
    def load(
        cls,
        repo_root: Path | str | None = None,
        profile: str | None = None,
        *,
        overrides: dict[str, Any] | None = None,
    ) -> Settings:
        """Resolve configuration for ``profile``.

        Args:
            repo_root: Repository root. Defaults to the package's grandparent.
            profile: ``dev`` or ``demo``. Falls back to ``RC_PROFILE``, then
                :data:`DEFAULT_PROFILE`.
            overrides: Highest-precedence topology overrides, deep-merged last.
                Intended for CLI flags and tests.

        Raises:
            ConfigError: If a file is missing, malformed, or the resolved
                configuration is internally inconsistent.
        """
        root = Path(repo_root).resolve() if repo_root else Path(__file__).resolve().parents[2]
        conf_dir = root / "conf"

        resolved_profile = (profile or _env_override("PROFILE") or DEFAULT_PROFILE).lower()
        if resolved_profile not in VALID_PROFILES:
            raise ConfigError(
                f"Unknown profile {resolved_profile!r}; expected one of {list(VALID_PROFILES)}"
            )

        merged = _read_yaml(conf_dir / "base.yml")
        merged = deep_merge(merged, _read_yaml(conf_dir / "profiles" / f"{resolved_profile}.yml"))

        domains = {key: _read_yaml(conf_dir / name) for key, name in _DOMAIN_FILES.items()}

        # `<domain>_overrides` in a profile tweaks a domain config without
        # duplicating it. Keeps dev.yml to a readable diff.
        for domain in domains:
            overlay = merged.pop(f"{domain}_overrides", None)
            if overlay:
                if not isinstance(overlay, dict):
                    raise ConfigError(
                        f"{resolved_profile}.yml: {domain}_overrides must be a mapping"
                    )
                domains[domain] = deep_merge(domains[domain], overlay)

        if overrides:
            merged = deep_merge(merged, overrides)

        # --- environment escape hatches ---------------------------------- #
        database = _env_override("DATABASE") or merged["database"]["name"]
        suffix = merged.get("project", {}).get("object_suffix", "") or ""

        warehouses = {
            key: Warehouse(**spec) for key, spec in (merged.get("warehouses") or {}).items()
        }
        if wh := _env_override("WAREHOUSE"):
            # Point every role at one warehouse -- used when developing against
            # a warehouse you did not create.
            forced = validate_identifier(wh, what="RC_WAREHOUSE")
            warehouses = {k: v.model_copy(update={"name": forced}) for k, v in warehouses.items()}

        roles_raw = dict(merged.get("roles") or {})
        if role := _env_override("ROLE"):
            roles_raw["admin"] = role

        if seed := _env_override("SEED"):
            try:
                domains["generation"]["seed"] = int(seed)
            except ValueError as exc:
                raise ConfigError(f"RC_SEED must be an integer, got {seed!r}") from exc

        if conn := _env_override("SNOWFLAKE_CONNECTION"):
            domains["external"].setdefault("snowflake", {})["connection_name"] = conn

        # Retention is what the profile asks for; the account decides what it
        # gets. Clamping here rather than at deploy time means every consumer --
        # sql_context, the deployer, doctor -- sees one already-reconciled value,
        # so a demo-profile deploy cannot fail on error 001048 and no caller has
        # to remember to apply the cap itself.
        try:
            retention_requested = {k: int(v) for k, v in (merged["retention"] or {}).items()}
        except KeyError as exc:
            raise ConfigError(f"Missing required configuration section: {exc}") from exc
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"retention values must be integers: {exc}") from exc

        retention_values = dict(retention_requested)
        clamp: RetentionClamp | None = None
        connection = str((domains["external"].get("snowflake") or {}).get("connection_name") or "")
        if connection and (ceiling := _retention_ceiling(root, connection)) is not None:
            max_days, evidence = ceiling
            if any(value > max_days for value in retention_values.values()):
                retention_values = {k: min(v, max_days) for k, v in retention_values.items()}
                clamp = RetentionClamp(
                    requested=retention_requested,
                    applied=retention_values,
                    max_days=max_days,
                    probe_id=evidence["probe_id"],
                    failure_class=evidence["failure_class"],
                    error_code=evidence["error_code"],
                    cap_source=evidence["cap_source"],
                    evidence_path=evidence["evidence_path"],
                )

        try:
            return cls(
                repo_root=root,
                profile=resolved_profile,
                object_suffix=suffix,
                database=validate_identifier(database, what="Database name"),
                schemas=Schemas(**merged["schemas"]),
                warehouses=warehouses,
                roles=Roles(**roles_raw),
                stages=Stages(**merged["stages"]),
                retention=Retention(**retention_values),
                retention_clamp=clamp,
                dynamic_tables={
                    key: DynamicTableSpec(**spec)
                    for key, spec in (merged.get("dynamic_tables") or {}).items()
                },
                views={
                    k: validate_identifier(v, what="View name")
                    for k, v in (merged.get("views") or {}).items()
                },
                streams_tasks=StreamsTasks(**merged["streams_tasks"]),
                cortex=CortexObjects(**merged["cortex"]),
                app=AppSpec(**merged["app"]),
                case_tools={
                    k: validate_identifier(str(v), what=f"case_tools.{k}")
                    for k, v in (merged.get("case_tools") or {}).items()
                },
                generation=domains["generation"],
                detection=domains["detection"],
                models=domains["models"],
                external=domains["external"],
                ml=domains["ml"],
            )
        except KeyError as exc:
            raise ConfigError(f"Missing required configuration section: {exc}") from exc

    # ------------------------------------------------------------------ #
    # Name resolution
    # ------------------------------------------------------------------ #

    def obj(self, name: str) -> str:
        """Apply the profile's object suffix to a bare object name.

        Keeps a ``dev`` deploy from colliding with ``demo`` objects in the same
        account.
        """
        return f"{name.upper()}{self.object_suffix.upper()}"

    def db(self) -> str:
        """Fully qualified database name for this profile."""
        return self.obj(self.database)

    def fq(self, schema_key: str, name: str | None = None) -> str:
        """Return ``DB.SCHEMA`` or ``DB.SCHEMA.OBJECT``, suffix applied.

        Args:
            schema_key: A key of :class:`Schemas` (``raw``, ``curated``, ...)
                rather than a literal schema name, so a schema rename is a
                one-line config change.
            name: Optional object name.
        """
        try:
            schema = getattr(self.schemas, schema_key)
        except AttributeError as exc:
            raise ConfigError(
                f"Unknown schema key {schema_key!r}; expected one of {list(Schemas.model_fields)}"
            ) from exc
        base = f"{self.db()}.{schema}"
        return f"{base}.{self.obj(name)}" if name else base

    def dt(self, key: str) -> DynamicTableSpec:
        """Dynamic table spec by config key."""
        try:
            return self.dynamic_tables[key]
        except KeyError as exc:
            raise ConfigError(
                f"Unknown dynamic table key {key!r}; expected one of {sorted(self.dynamic_tables)}"
            ) from exc

    def warehouse(self, key: str) -> Warehouse:
        try:
            return self.warehouses[key]
        except KeyError as exc:
            raise ConfigError(
                f"Unknown warehouse key {key!r}; expected one of {sorted(self.warehouses)}"
            ) from exc

    @cached_property
    def replay(self) -> ReplaySpec:
        """The typed ``replay`` block of ``conf/generation.yml``."""
        return _replay_spec(self.generation)

    @cached_property
    def connection_name(self) -> str:
        """Snowflake connection name from ``~/.snowflake/connections.toml``."""
        name = (self.external.get("snowflake") or {}).get("connection_name")
        if not name:
            raise ConfigError(
                "No Snowflake connection configured. Set "
                "conf/external.yml:snowflake.connection_name or RC_SNOWFLAKE_CONNECTION."
            )
        return str(name)

    def model_for(self, role: str) -> list[str]:
        """Ordered model candidates for a logical agent role.

        A *list* is returned on purpose: model availability is region-dependent,
        so the caller picks the first name the doctor confirmed rather than
        trusting a single hardcoded literal.
        """
        roles = self.models.get("roles") or {}
        if role not in roles:
            raise ConfigError(
                f"No model configured for role {role!r}; known roles: {sorted(roles)}"
            )
        candidates = roles[role].get("candidates") or []
        if not candidates:
            raise ConfigError(f"Model role {role!r} declares no candidates")
        return [str(c) for c in candidates]

    # ------------------------------------------------------------------ #
    # SQL rendering
    # ------------------------------------------------------------------ #

    def sql_context(self) -> dict[str, Any]:
        """Jinja context for rendering ``sql/**/*.sql``.

        Every identifier a SQL template needs is provided here, which is what
        lets us forbid literal object names in ``.sql`` files. ``sql/README.md``
        documents the contract.
        """
        return {
            "profile": self.profile,
            "suffix": self.object_suffix.upper(),
            "db": self.db(),
            "schema": {key: getattr(self.schemas, key) for key in Schemas.model_fields},
            "fq": {key: self.fq(key) for key in Schemas.model_fields},
            "wh": {key: self.obj(w.name) for key, w in self.warehouses.items()},
            "wh_spec": {key: w.model_dump() for key, w in self.warehouses.items()},
            "role": {
                "admin": self.obj(self.roles.admin),
                "service": self.obj(self.roles.service),
                "compliance_officer": self.obj(self.roles.compliance_officer),
                "branch_analysts": {
                    region: self.obj(role) for region, role in self.roles.branch_analysts.items()
                },
                "public_app": self.obj(self.roles.public_app) if self.roles.public_app else "",
                "public_officer": (
                    self.obj(self.roles.public_officer) if self.roles.public_officer else ""
                ),
            },
            "app": {
                "public_user": self.obj(self.app.public_user) if self.app.public_user else "",
                "public_officer_user": (
                    self.obj(self.app.public_officer_user) if self.app.public_officer_user else ""
                ),
                "warehouse": self.obj(self.warehouse(self.app.warehouse).name),
            },
            "stage": {key: self.obj(getattr(self.stages, key)) for key in Stages.model_fields},
            "retention": self.retention.model_dump(),
            # Present so a rendered DDL header can state that retention was
            # reduced and on what evidence, rather than the reader wondering why
            # the deployed value differs from the profile.
            "retention_clamp": (
                self.retention_clamp.model_dump() if self.retention_clamp else None
            ),
            "dt": {
                key: {"name": self.obj(spec.name), "target_lag": spec.target_lag}
                for key, spec in self.dynamic_tables.items()
            },
            "view": {key: self.obj(name) for key, name in self.views.items()},
            # Case workflow procedures and UDFs, suffixed; the two data values
            # (template id, narrative author) are not object names, so unsuffixed.
            "tool": {
                key: (name if key in _CASE_TOOL_DATA else self.obj(name))
                for key, name in self.case_tools.items()
            },
            "stream_task": {
                "signals_stream": self.obj(self.streams_tasks.signals_stream),
                "route_signals_procedure": self.obj(self.streams_tasks.route_signals_procedure),
                "route_signals_task": self.obj(self.streams_tasks.route_signals_task),
                "resume_route_task": self.streams_tasks.resume_route_task,
            },
            # conf/detection.yml `routing` as typed SQL literals, for ROUTE_SIGNALS.
            # Operational settings, not POLICY_CLAUSE thresholds.
            "routing": _routing_context(self.detection),
            # ids.signal_id / ids.case_id, so ROUTE_SIGNALS derives the same ids
            # in SQL as Python does (the live proof compares them).
            "routed_id": {
                "signal_prefix": _sql_literal(ids.SIGNAL_ID_PREFIX, "VARCHAR"),
                "case_prefix": _sql_literal(ids.CASE_ID_PREFIX, "VARCHAR"),
                "hex": ids.ROUTED_ID_HEX,
            },
            "cortex": {
                "search_service": self.obj(self.cortex.search_service),
                "search_source_table": self.obj(self.cortex.search_source_table),
                "search_target_lag": self.cortex.search_target_lag,
                "semantic_view": self.obj(self.cortex.semantic_view),
                "agent": self.obj(self.cortex.agent),
                "agent_schema_fq": (
                    f"{self.cortex.agent_database}.{self.cortex.agent_schema}"
                    if self.cortex.agent_database and self.cortex.agent_schema
                    else ""
                ),
                "agent_database": self.cortex.agent_database,
                "ai_str": {
                    "cooldown_minutes": self.cortex.ai_str_cooldown_minutes,
                    "daily_runs": self.cortex.ai_str_daily_runs,
                    "reporting_bank": self.cortex.reporting_bank,
                    "principal_officer": self.cortex.principal_officer,
                },
            },
            # Exposed so SQL can reference structural ML settings (function
            # names, model names). Thresholds still come from POLICY_CLAUSE.
            "ml": self.ml,
            "external": self.external,
            # conf/detection.yml as typed, escaped SQL literals for sql/05_policy,
            # the one stage allowed to carry threshold values (as data, into
            # POLICY_CLAUSE). Validated on build: an ungoverned row fails here.
            "policy_seed": _policy_seed(self.detection, self.repo_root / "sql"),
            # Per rule: its id, version, clause ids by role and window anchor, as
            # literals, for sql/rules/<rule_id>.sql. Names, never thresholds.
            "rule": _rule_context(self.detection),
            # The bank's own accounts (the cash desk), as a literal: CURATED
            # flags them so rules and graph edges can leave them out (D4).
            "internal": {
                "bank_id": _sql_literal(
                    str(self.generation["internal_accounts"]["src_bank_id"]), "VARCHAR"
                )
            },
            # The evaluation grain's month format, so `verify` maps a signal's
            # evidence legs onto EVAL_GRAIN_PARTY_MONTH exactly as generation
            # built it (D40).
            "eval_grain": {"month_format": _sql_literal(_month_bucket_sql_format(), "VARCHAR")},
            # The illustrative ALM book as ALM_CASHFLOW rows, for sql/01_ddl/05_liquidity.
            # Amounts and clause ids only; the tolerance limits stay in POLICY_CLAUSE.
            "alm_book": _alm_book_rows(self.generation, self.detection),
            # The case-event alert (sql/10_automations). The recipient comes from the
            # environment, never from a committed file; empty means skip the stage.
            "case_alert": self._case_alert_context(),
            # The live simulator (sql/procedures/08_simulator.sql), from the same
            # `replay` block the CLI replay uses: one tick releases sim_step of
            # booking time, cut early at batch_legs.
            "sim": self._sim_context(),
        }

    def _sim_context(self) -> dict[str, Any]:
        if "replay" not in self.generation:
            return {"step_seconds": 0, "batch_legs": 0, "hold_back_from": "NULL"}
        spec = _replay_spec(self.generation)
        cut = spec.hold_back_from
        return {
            "step_seconds": spec.sim_step_seconds,
            "batch_legs": spec.batch_legs,
            "hold_back_from": (
                "NULL"
                if cut is None
                # A fixed-format timestamp, so no quote can occur in it.
                else f"'{cut.strftime('%Y-%m-%d %H:%M:%S')}'::TIMESTAMP_NTZ"
            ),
        }

    def _case_alert_context(self) -> dict[str, Any]:
        notifications = self.external.get("notifications") or {}
        spec = notifications.get("case_alert") or {}
        recipient = (os.environ.get(str(spec.get("recipient_env") or "")) or "").strip()
        if recipient and not re.fullmatch(r"[^@\s',]+@[^@\s',]+\.[^@\s',]+", recipient):
            raise ConfigError(f"{spec.get('recipient_env')} is not an email address")
        if not spec:
            return {"recipient": ""}
        schedule = str(spec["schedule"]).strip().upper()
        if not re.fullmatch(r"\d+ MINUTES?", schedule):
            raise ConfigError(f"notifications.case_alert.schedule {schedule!r}: use 'N MINUTE'")
        # The address passed the pattern above, which admits no quote or comma.
        return {
            "recipient": recipient,
            "recipient_literal": f"'{recipient}'" if recipient else "",
            "integration": self.obj(str(notifications["integration_name"])),
            "alert": self.obj(str(spec["alert_name"])),
            "procedure": self.obj(str(spec["procedure_name"])),
            "schedule": f"'{schedule}'",
            "resume": bool(spec.get("resume")),
        }


def _policy_seed(detection: dict[str, Any], sql_root: Path) -> dict[str, list[dict[str, str]]]:
    # Imported here: risk_copilot.policy imports ConfigError from this module.
    from risk_copilot.policy import policy_seed

    return policy_seed(detection, sql_root)


def _rule_context(detection: dict[str, Any]) -> dict[str, dict[str, Any]]:
    from risk_copilot.policy import rule_context

    return rule_context(detection)


def _routing_context(detection: dict[str, Any]) -> dict[str, str]:
    from risk_copilot.policy import routing_context

    return routing_context(detection)


def _alm_book_rows(generation: dict[str, Any], detection: dict[str, Any]) -> list[dict[str, str]]:
    from risk_copilot.policy import alm_book_rows

    return alm_book_rows(generation, detection)


def _sql_literal(value: Any, sql_type: str) -> str:
    from risk_copilot.policy import sql_literal

    return sql_literal(value, sql_type)


def _month_bucket_sql_format() -> str:
    # Imported here: generation modules pull in pyarrow, which the CLI's
    # config-only commands do not need.
    from risk_copilot.generation.eval_grain import MONTH_BUCKET_SQL_FORMAT

    return MONTH_BUCKET_SQL_FORMAT


def load_settings(
    repo_root: Path | str | None = None,
    profile: str | None = None,
    **overrides: Any,
) -> Settings:
    """Convenience wrapper around :meth:`Settings.load`."""
    return Settings.load(repo_root=repo_root, profile=profile, overrides=overrides or None)
