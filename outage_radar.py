#!/usr/bin/env python3
"""Offline, conservative correlation of synthetic or user-supplied probe snapshots."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


class InputError(ValueError):
    """Raised when the JSON document does not match the supported input schema."""


ROOT_KEYS = {
    "schema_version",
    "observed_at",
    "sites",
    "minimum_quorum_sites",
    "services",
    "service_checks",
    "dependency_checks",
}
STATUS_VALUES = {"ok", "fail", "unknown"}
CLASSIFICATIONS = {
    "likely service-wide",
    "site-local/connectivity-path",
    "dependency-wide",
    "insufficient evidence/unknown",
    "no observed failure",
}


def _exact_keys(value: dict[str, Any], expected: set[str], where: str) -> None:
    actual = set(value)
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    if missing or extra:
        details = []
        if missing:
            details.append("missing keys: " + ", ".join(missing))
        if extra:
            details.append("unexpected keys: " + ", ".join(extra))
        raise InputError(f"{where}: " + "; ".join(details))


def _object(value: Any, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise InputError(f"{where} must be an object")
    return value


def _string(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise InputError(f"{where} must be a non-empty string without surrounding whitespace")
    return value


def _string_list(value: Any, where: str, *, allow_empty: bool) -> list[str]:
    if not isinstance(value, list) or (not allow_empty and not value):
        qualifier = "a list" if allow_empty else "a non-empty list"
        raise InputError(f"{where} must be {qualifier} of strings")
    result = [_string(item, f"{where}[{index}]") for index, item in enumerate(value)]
    if len(result) != len(set(result)):
        raise InputError(f"{where} must not contain duplicates")
    return result


def _status(value: Any, where: str) -> str:
    if not isinstance(value, str) or value not in STATUS_VALUES:
        raise InputError(f"{where} must be one of: fail, ok, unknown")
    return value


def validate_input(document: Any) -> dict[str, Any]:
    """Validate the complete supported schema and return the original document."""
    root = _object(document, "input")
    _exact_keys(root, ROOT_KEYS, "input")

    if type(root["schema_version"]) is not int or root["schema_version"] != 1:
        raise InputError("schema_version must be the integer 1")

    observed_at = _string(root["observed_at"], "observed_at")
    try:
        timestamp = datetime.fromisoformat(observed_at[:-1] + "+00:00" if observed_at.endswith("Z") else observed_at)
    except ValueError as exc:
        raise InputError("observed_at must be an ISO 8601 timestamp with a timezone") from exc
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise InputError("observed_at must include a timezone, such as Z or +00:00")

    sites = _string_list(root["sites"], "sites", allow_empty=False)
    services_value = root["services"]
    if not isinstance(services_value, list) or not services_value:
        raise InputError("services must be a non-empty list")
    service_ids: list[str] = []
    service_dependencies: dict[str, list[str]] = {}
    declared_dependencies: set[str] = set()
    for index, raw_service in enumerate(services_value):
        where = f"services[{index}]"
        service = _object(raw_service, where)
        _exact_keys(service, {"id", "dependencies"}, where)
        service_id = _string(service["id"], f"{where}.id")
        dependencies = _string_list(service["dependencies"], f"{where}.dependencies", allow_empty=True)
        service_ids.append(service_id)
        service_dependencies[service_id] = dependencies
        declared_dependencies.update(dependencies)
    if len(service_ids) != len(set(service_ids)):
        raise InputError("services must have unique ids")

    quorum = root["minimum_quorum_sites"]
    if type(quorum) is not int or not 2 <= quorum <= len(sites):
        raise InputError("minimum_quorum_sites must be an integer from 2 through the number of sites")

    service_checks = _validate_checks(
        root["service_checks"],
        "service_checks",
        {"site", "service", "status"},
        sites=set(sites),
        entities=set(service_ids),
        entity_field="service",
    )
    dependency_checks = _validate_checks(
        root["dependency_checks"],
        "dependency_checks",
        {"site", "dependency", "status"},
        sites=set(sites),
        entities=declared_dependencies,
        entity_field="dependency",
    )
    return root


def _validate_checks(
    value: Any,
    where: str,
    expected_keys: set[str],
    *,
    sites: set[str],
    entities: set[str],
    entity_field: str,
) -> dict[tuple[str, str], str]:
    if not isinstance(value, list):
        raise InputError(f"{where} must be a list")
    result: dict[tuple[str, str], str] = {}
    for index, raw_check in enumerate(value):
        item_where = f"{where}[{index}]"
        check = _object(raw_check, item_where)
        _exact_keys(check, expected_keys, item_where)
        site = _string(check["site"], f"{item_where}.site")
        entity = _string(check[entity_field], f"{item_where}.{entity_field}")
        if site not in sites:
            raise InputError(f"{item_where}.site references undeclared site {site!r}")
        if entity not in entities:
            raise InputError(f"{item_where}.{entity_field} references undeclared {entity_field} {entity!r}")
        key = (site, entity)
        if key in result:
            raise InputError(f"{where} contains duplicate check for site {site!r} and {entity_field} {entity!r}")
        result[key] = _status(check["status"], f"{item_where}.status")
    return result


def _sites_with_status(
    checks: dict[tuple[str, str], str], entity: str, status: str
) -> set[str]:
    return {site for (site, item), value in checks.items() if item == entity and value == status}


def _classify_service(
    service_id: str,
    dependencies: list[str],
    sites: list[str],
    quorum: int,
    service_checks: dict[tuple[str, str], str],
    dependency_checks: dict[tuple[str, str], str],
) -> dict[str, Any]:
    failed = _sites_with_status(service_checks, service_id, "fail")
    healthy = _sites_with_status(service_checks, service_id, "ok")
    unknown = _sites_with_status(service_checks, service_id, "unknown")
    known = failed | healthy
    missing = set(sites) - failed - healthy - unknown

    dependency_evidence: list[dict[str, Any]] = []
    matching_dependency_failures: list[tuple[str, set[str]]] = []
    for dependency in sorted(dependencies):
        dep_failed = _sites_with_status(dependency_checks, dependency, "fail")
        dep_healthy = _sites_with_status(dependency_checks, dependency, "ok")
        dep_unknown = _sites_with_status(dependency_checks, dependency, "unknown")
        dep_missing = set(sites) - dep_failed - dep_healthy - dep_unknown
        overlapping_failures = failed & dep_failed
        dependency_evidence.append(
            {
                "dependency": dependency,
                "failed_sites": sorted(dep_failed),
                "healthy_sites": sorted(dep_healthy),
                "unknown_sites": sorted(dep_unknown),
                "missing_sites": sorted(dep_missing),
                "co_failing_service_sites": sorted(overlapping_failures),
            }
        )
        if len(overlapping_failures) >= quorum:
            matching_dependency_failures.append((dependency, overlapping_failures))

    if matching_dependency_failures:
        dependency, overlap = sorted(matching_dependency_failures, key=lambda item: item[0])[0]
        classification = "dependency-wide"
        basis = (
            f"The service and declared dependency {dependency!r} both failed at "
            f"{len(overlap)} sites, meeting the {quorum}-site quorum. This is correlated scope evidence, not proof of cause."
        )
    elif len(failed) >= quorum:
        classification = "likely service-wide"
        basis = (
            f"Service failures were observed at {len(failed)} sites, meeting the {quorum}-site quorum. "
            "This describes observed scope, not a confirmed cause."
        )
    elif len(known) >= quorum and len(failed) == 1 and bool(healthy):
        classification = "site-local/connectivity-path"
        basis = (
            f"Failures are confined to {next(iter(failed))!r}, while {len(healthy)} site(s) report success. "
            "This is a site-local/connectivity-path signal, not a confirmed cause."
        )
    elif not failed and len(known) >= quorum:
        classification = "no observed failure"
        basis = (
            f"No failures were observed among {len(known)} usable site result(s); "
            f"the minimum quorum is {quorum}. Missing and unknown probes are listed separately."
        )
    else:
        classification = "insufficient evidence/unknown"
        if len(known) < quorum:
            basis = (
                f"Only {len(known)} site(s) have usable service results, below the {quorum}-site minimum quorum; "
                "the failure pattern cannot be classified confidently."
            )
        elif failed:
            basis = (
                f"Failures were observed at {len(failed)} site(s), but they neither meet the {quorum}-site quorum "
                "nor form a single-site failure pattern with corroborating successes."
            )
        else:
            basis = "No usable service result establishes an outage signal."

    return {
        "service": service_id,
        "classification": classification,
        "basis": basis,
        "evidence": {
            "minimum_quorum_sites": quorum,
            "usable_sites_count": len(known),
            "quorum_met": len(known) >= quorum,
            "failed_sites": sorted(failed),
            "healthy_sites": sorted(healthy),
            "unknown_sites": sorted(unknown),
            "missing_sites": sorted(missing),
            "dependencies": dependency_evidence,
        },
    }


def analyze(document: Any) -> dict[str, Any]:
    """Return deterministic, conservative per-service classifications for a snapshot."""
    data = validate_input(document)
    sites = sorted(data["sites"])
    quorum = data["minimum_quorum_sites"]
    service_checks = {
        (item["site"], item["service"]): item["status"] for item in data["service_checks"]
    }
    dependency_checks = {
        (item["site"], item["dependency"]): item["status"] for item in data["dependency_checks"]
    }
    service_definitions = sorted(data["services"], key=lambda item: item["id"])
    return {
        "schema_version": 1,
        "observed_at": data["observed_at"],
        "minimum_quorum_sites": quorum,
        "signals": [
            _classify_service(
                service["id"],
                service["dependencies"],
                sites,
                quorum,
                service_checks,
                dependency_checks,
            )
            for service in service_definitions
        ],
    }


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise InputError(f"duplicate JSON object key {key!r}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise InputError(f"invalid JSON numeric constant {value!r}")


def _render_json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Analyze an offline JSON snapshot of organization-owned service probes."
    )
    parser.add_argument("--input", required=True, type=Path, help="path to an input JSON snapshot")
    parser.add_argument("--output", type=Path, help="write result JSON here instead of stdout")
    args = parser.parse_args(argv)

    try:
        raw = args.input.read_text(encoding="utf-8")
        document = json.loads(raw, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
        result = analyze(document)
        rendered = _render_json(result)
        if args.output:
            args.output.write_text(rendered, encoding="utf-8")
        else:
            sys.stdout.write(rendered)
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except json.JSONDecodeError as exc:
        print(f"error: invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}", file=sys.stderr)
        return 2
    except InputError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
