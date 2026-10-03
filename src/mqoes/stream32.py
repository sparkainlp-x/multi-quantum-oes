"""Float-level, offline reference model of the supplied OES32 streaming branch logic."""
from __future__ import annotations

import math
from typing import Any

from .contracts import ValidationError

TAU_MIN = 0.05
TAU_MAX = 4.0
ETA = 0.05
LEAK = 0.95


def _finite(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"{field} must be a finite number")
    try:
        number = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValidationError(f"{field} must be finite") from exc
    if not math.isfinite(number):
        raise ValidationError(f"{field} must be finite")
    return number


def simulate_stream32(document: Any) -> dict[str, Any]:
    """Model the packet-by-packet math from oes32_triage.cpp using Python floats.

    This is not bit-exact HLS: the supplied snippet omits the type/header definitions
    needed to recover fixed-point rounding and AXI word packing behavior.
    """
    expected = {"tau_sensitivity", "rate_limit_threshold", "reset_state", "packets"}
    if not isinstance(document, dict) or set(document) != expected:
        raise ValidationError("stream32 input must contain exactly tau_sensitivity, rate_limit_threshold, reset_state, and packets")
    sensitivity = _finite(document["tau_sensitivity"], "tau_sensitivity")
    threshold = _finite(document["rate_limit_threshold"], "rate_limit_threshold")
    if type(document["reset_state"]) is not bool:
        raise ValidationError("reset_state must be a boolean")
    packets = document["packets"]
    if not isinstance(packets, list) or not packets:
        raise ValidationError("packets must be a non-empty array")
    tau = 1.0
    if document["reset_state"]:
        # The HLS snippet assigns tau_sensitivity directly on reset; it is not
        # clamped until the packet branch updates state.
        tau = sensitivity
    primary: list[dict[str, Any]] = []
    residual: list[dict[str, Any]] = []
    trace: list[dict[str, Any]] = []
    for index, item in enumerate(packets):
        if not isinstance(item, dict) or set(item) != {"data_value", "is_shock"}:
            raise ValidationError(f"packets[{index}] must contain exactly data_value and is_shock")
        value = _finite(item["data_value"], f"packets[{index}].data_value")
        if type(item["is_shock"]) is not bool:
            raise ValidationError(f"packets[{index}].is_shock must be a boolean")
        tau_before = tau
        weighted = value * tau_before
        if not math.isfinite(weighted):
            raise ValidationError(f"packets[{index}] weighted value exceeds finite float range")
        diverted = item["is_shock"] or weighted > threshold
        if diverted:
            route = "residual"
            error = weighted - threshold
            tau_after = min(TAU_MAX, max(TAU_MIN, tau_before - error * ETA))
            residual.append({"data_value": value, "is_shock": item["is_shock"]})
        else:
            route = "primary"
            tau_after = min(TAU_MAX, max(TAU_MIN, tau_before * LEAK + sensitivity * ETA))
            primary.append({"data_value": value, "is_shock": item["is_shock"]})
        trace.append({
            "packet_index": index,
            "data_value": value,
            "is_shock": item["is_shock"],
            "adaptive_tau_before": tau_before,
            "weighted_value": weighted,
            "rate_limit_threshold": threshold,
            "route": route,
            "routing_reason": "shock flag" if item["is_shock"] else ("weighted value > threshold" if weighted > threshold else "weighted value <= threshold"),
            "adaptive_tau_after": tau_after,
        })
        tau = tau_after
    return {
        "contract": "OES32 adaptive-tau packet routing (float-level software model)",
        "scope": "separate from OES-512 weighted replay scoring and OES32-residual pairwise comparison",
        "parameters": {"tau_sensitivity": sensitivity, "rate_limit_threshold": threshold, "reset_state": document["reset_state"]},
        "primary_stream": primary,
        "residual_stream": residual,
        "adaptive_tau_observations": [row["adaptive_tau_after"] for row in trace],
        "packet_trace": trace,
        "model_limits": [
            "Python float-level equation model only; no FPGA, AXI transport, timing, synthesis, or bit-exact fixed-point validation.",
            "The code snippet's missing typedef/header details prevent an exact data-width and rounding model.",
            "Input/output values here are illustrative numbers, not recorded device telemetry.",
        ],
    }
