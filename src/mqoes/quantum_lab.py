"""Tiny educational statevector/quantum-style experiments, isolated from OES decisions."""
from __future__ import annotations

import cmath
import itertools
import math
from typing import Any


def _apply_gate(state: tuple[complex, complex], gate: dict[str, Any]) -> tuple[complex, complex]:
    a, b = state
    name = gate["gate"]
    if name == "H":
        scale = 1 / math.sqrt(2)
        return ((a + b) * scale, (a - b) * scale)
    if name == "X":
        return b, a
    if name == "RZ":
        angle = float(gate["angle"])
        return a * cmath.exp(-0.5j * angle), b * cmath.exp(0.5j * angle)
    raise ValueError(f"unsupported one-qubit gate: {name}")


def simulate_one_qubit(circuit: list[dict[str, Any]]) -> tuple[complex, complex]:
    state = (1 + 0j, 0 + 0j)
    for gate in circuit:
        state = _apply_gate(state, gate)
    return state


def _equivalent_up_to_global_phase(a: tuple[complex, complex], b: tuple[complex, complex], tolerance: float = 1e-10) -> bool:
    pivot = next((i for i in range(2) if abs(a[i]) > tolerance and abs(b[i]) > tolerance), None)
    if pivot is None:
        return max(abs(a[i] - b[i]) for i in range(2)) <= tolerance
    phase = a[pivot] / b[pivot]
    return abs(abs(phase) - 1.0) <= tolerance and all(abs(a[i] - phase * b[i]) <= tolerance for i in range(2))


def circuit_unitary(circuit: list[dict[str, Any]]) -> tuple[tuple[complex, complex], tuple[complex, complex]]:
    """Return the 2x2 unitary as its two columns: the images of |0> and |1>."""
    col0, col1 = (1 + 0j, 0 + 0j), (0 + 0j, 1 + 0j)
    for gate in circuit:
        col0, col1 = _apply_gate(col0, gate), _apply_gate(col1, gate)
    return col0, col1


def circuits_equivalent_up_to_global_phase(first: list[dict[str, Any]], second: list[dict[str, Any]], tolerance: float = 1e-10) -> bool:
    """Compare full one-qubit unitaries up to a single shared global phase.

    Checking only the |0> output state is insufficient: RZ acts on |0> as a pure
    phase, so dropping or mis-merging an RZ could go undetected.
    """
    u = circuit_unitary(first)
    v = circuit_unitary(second)
    flat_u = (u[0][0], u[0][1], u[1][0], u[1][1])
    flat_v = (v[0][0], v[0][1], v[1][0], v[1][1])
    pivot = max(range(4), key=lambda i: abs(flat_v[i]))
    if abs(flat_v[pivot]) <= tolerance:
        return False
    phase = flat_u[pivot] / flat_v[pivot]
    if abs(abs(phase) - 1.0) > tolerance:
        return False
    return all(abs(flat_u[i] - phase * flat_v[i]) <= tolerance for i in range(4))


def compile_toy_circuit(circuit: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Apply only local identities: HH=XX=I and adjacent RZ angles add."""
    stack: list[dict[str, Any]] = []
    for gate in circuit:
        name = gate.get("gate")
        if name not in {"H", "X", "RZ"}:
            raise ValueError("toy compiler supports only H, X, and RZ")
        current = {"gate": name}
        if name == "RZ":
            angle = gate.get("angle")
            if isinstance(angle, bool) or not isinstance(angle, (int, float)) or not math.isfinite(float(angle)):
                raise ValueError("RZ angle must be a finite number")
            current["angle"] = float(angle)
        if name in {"H", "X"} and stack and stack[-1]["gate"] == name:
            stack.pop()
        elif name == "RZ" and stack and stack[-1]["gate"] == "RZ":
            combined = stack.pop()["angle"] + current["angle"]
            if abs(math.remainder(combined, 2 * math.pi)) > 1e-12:
                stack.append({"gate": "RZ", "angle": combined})
        elif name == "RZ" and abs(math.remainder(current["angle"], 2 * math.pi)) <= 1e-12:
            continue
        else:
            stack.append(current)
    return stack


def quantum_kernel(x: float, y: float) -> float:
    """One-qubit feature map overlap |<psi(x)|psi(y)>|^2 for x,y in [0,1]."""
    if not (0 <= x <= 1 and 0 <= y <= 1):
        raise ValueError("kernel inputs must be normalized to [0, 1]")
    return math.cos(math.pi * (x - y) / 2) ** 2


def toy_kernel_classifier() -> dict[str, Any]:
    training = [
        {"x": 0.05, "label": "low"},
        {"x": 0.20, "label": "low"},
        {"x": 0.35, "label": "low"},
        {"x": 0.70, "label": "high"},
        {"x": 0.85, "label": "high"},
        {"x": 0.95, "label": "high"},
    ]
    results = []
    for query in (0.10, 0.50, 0.90):
        totals = {
            label: math.fsum(quantum_kernel(query, row["x"]) for row in training if row["label"] == label)
            for label in ("high", "low")
        }
        prediction = sorted(totals, key=lambda label: (-totals[label], label))[0]
        results.append({"query": query, "class_kernel_similarity_sums": totals, "predicted_label": prediction})
    return {
        "method": "one-qubit fidelity-kernel nearest-class-similarity toy",
        "feature_map": "|psi(x)> = cos(pi*x/2)|0> + sin(pi*x/2)|1>",
        "training_samples": training,
        "predictions": results,
        "note": "Tiny hand-coded simulation on invented scalar examples; not a trained/validated detector, QPU run, or source of operational alerts.",
    }


def _qubo_energy(bits: tuple[int, ...], linear: tuple[float, ...], edges: tuple[tuple[int, int, float], ...]) -> float:
    energy = sum(linear[i] * bits[i] for i in range(len(bits)))
    energy += sum(weight * bits[i] * bits[j] for i, j, weight in edges)
    return energy


def toy_qubo_optimization() -> dict[str, Any]:
    """Solve a four-node max-cut QUBO exactly and with a simple bit-flip baseline."""
    node_count = 4
    graph_edges = ((0, 1), (1, 2), (2, 3), (3, 0), (0, 2))
    linear = [0.0] * node_count
    quadratic = []
    for i, j in graph_edges:
        # Negative cut indicator: -(x_i + x_j - 2*x_i*x_j).
        linear[i] -= 1.0
        linear[j] -= 1.0
        quadratic.append((i, j, 2.0))
    linear_tuple = tuple(linear)
    edge_tuple = tuple(quadratic)
    scored = [(bits, _qubo_energy(bits, linear_tuple, edge_tuple)) for bits in itertools.product((0, 1), repeat=node_count)]
    best_bits, best_energy = min(scored, key=lambda pair: (pair[1], pair[0]))
    current = (0, 0, 0, 0)
    current_energy = _qubo_energy(current, linear_tuple, edge_tuple)
    flips = 0
    while True:
        options = []
        for i in range(node_count):
            candidate = list(current)
            candidate[i] = 1 - candidate[i]
            candidate_tuple = tuple(candidate)
            options.append(( _qubo_energy(candidate_tuple, linear_tuple, edge_tuple), candidate_tuple))
        energy, candidate = min(options, key=lambda pair: (pair[0], pair[1]))
        if energy >= current_energy:
            break
        current, current_energy = candidate, energy
        flips += 1
    return {
        "problem": "four-node unweighted max-cut encoded as a QUBO; minimize negative cut size",
        "qubo": {"linear": list(linear_tuple), "quadratic_terms": [{"i": i, "j": j, "coefficient": w} for i, j, w in edge_tuple]},
        "exact_exhaustive": {"assignments_evaluated": len(scored), "bits": list(best_bits), "energy": best_energy, "cut_edges": sum(best_bits[i] != best_bits[j] for i, j in graph_edges)},
        "classical_greedy_bit_flip": {"starting_bits": [0] * node_count, "bits": list(current), "energy": current_energy, "flips": flips},
        "note": "Exact enumeration is practical here because the toy has four binary variables. No quantum optimizer or quantum advantage is demonstrated.",
    }


def toy_circuit_compilation() -> dict[str, Any]:
    original = [
        {"gate": "H"}, {"gate": "H"},
        {"gate": "RZ", "angle": 0.4}, {"gate": "RZ", "angle": -0.1},
        {"gate": "X"}, {"gate": "X"}, {"gate": "H"},
    ]
    compiled = compile_toy_circuit(original)
    state_before, state_after = simulate_one_qubit(original), simulate_one_qubit(compiled)
    before_probs = [round(abs(value) ** 2, 12) for value in state_before]
    after_probs = [round(abs(value) ** 2, 12) for value in state_after]
    return {
        "gate_set": ["H", "X", "RZ"],
        "original_circuit": original,
        "compiled_circuit": compiled,
        "original_gate_count": len(original),
        "compiled_gate_count": len(compiled),
        "state_probabilities_before": before_probs,
        "state_probabilities_after": after_probs,
        "equivalent_up_to_global_phase": circuits_equivalent_up_to_global_phase(original, compiled),
        "note": "Tiny one-qubit rewrite demonstration; not a device-specific or fault-tolerant compiler.",
    }


def run_quantum_lab() -> dict[str, Any]:
    return {
        "scope": "isolated educational toy simulations; no QPU, external service, or operational OES input/output",
        "not_used_for_triage": True,
        "demos": {
            "quantum_machine_learning": toy_kernel_classifier(),
            "quantum_optimization": toy_qubo_optimization(),
            "circuit_synthesis_compilation": toy_circuit_compilation(),
            "chemical_ai": {"status": "not_implemented", "reason": "The supplied discussion provides no specific chemistry target, data, or validated simulation requirement; forcing chemical AI into telemetry triage would be unrelated."},
        },
    }
