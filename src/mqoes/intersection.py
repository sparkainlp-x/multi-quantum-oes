"""AI ∩ Quantum Computing: one small exact statevector core, four thin applications.

The centre of the familiar Venn diagram lists four topics. Each one here is a short,
readable application of the *same* core:

    core          an n-qubit statevector, a few gates, Pauli-sum observables, and
                  parameter-shift gradient descent
    1. QML        fidelity (quantum) kernel + nearest-centroid classifier vs. classical RBF
    2. QAOA       MaxCut on a 5-node graph, p = 1 and 2, vs. exact brute force
    3. Compile    wire-adjacent cancel/merge pass, verified by full-unitary equivalence
    4. Chemistry  VQE for the 2-qubit H2 Hamiltonian at 0.735 Å vs. exact diagonalisation

Honesty statement. Everything below is an exact *classical* simulation of toy problems
using Python floats. It is not a QPU run, not evidence of quantum advantage, and it is not
connected to the OES detector: nothing here is imported by, or reads the outputs of, the
replay-triage path. Standard library only; results are deterministic.

Conventions: qubit q is bit q of a basis-state index (little-endian). Pauli labels are
written as in Qiskit, where the right-most character acts on qubit 0.
"""
from __future__ import annotations

from dataclasses import dataclass
import cmath
import itertools
import math
import random
import textwrap
from typing import Callable, Iterable, Sequence

State = list[complex]
PauliSum = tuple[tuple[float, str], ...]  # ((coefficient, "XZ…"), …)

DISCLAIMER = ("Exact classical statevector simulation of toy problems; not a QPU run, not evidence "
              "of quantum advantage, and not connected to the OES detector.")

# ─────────────────────────────── core ───────────────────────────────


@dataclass(frozen=True)
class Param:
    """A reference to entry `index` of a parameter vector, multiplied by `scale`."""
    index: int
    scale: float = 1.0


@dataclass(frozen=True)
class Gate:
    """One gate. Rotations (RX, RY, RZ, RZZ) are exp(-i·angle/2·P) and carry an angle or a Param."""
    name: str
    qubits: tuple[int, ...]
    angle: float | Param | None = None


ROTATIONS = {"RX", "RY", "RZ", "RZZ"}
SELF_INVERSE = {"H", "X", "Z", "CNOT", "CZ"}


@dataclass(frozen=True)
class Circuit:
    n_qubits: int
    gates: tuple[Gate, ...]

    def bind(self, params: Sequence[float] = ()) -> "Circuit":
        """Replace every Param with its numeric angle."""
        def resolve(a: float | Param | None) -> float | None:
            return params[a.index] * a.scale if isinstance(a, Param) else a
        return Circuit(self.n_qubits, tuple(Gate(g.name, g.qubits, resolve(g.angle)) for g in self.gates))


def basis_state(n: int, k: int = 0) -> State:
    """|k⟩ on n qubits."""
    return [1 + 0j if i == k else 0j for i in range(2 ** n)]


def _one_qubit_matrix(gate: Gate) -> tuple[complex, complex, complex, complex]:
    name, t = gate.name, float(gate.angle or 0.0)
    c, s = math.cos(t / 2), math.sin(t / 2)
    return {
        "H": (1 / math.sqrt(2), 1 / math.sqrt(2), 1 / math.sqrt(2), -1 / math.sqrt(2)),
        "X": (0, 1, 1, 0),
        "Z": (1, 0, 0, -1),
        "RX": (c, -1j * s, -1j * s, c),
        "RY": (c, -s, s, c),
        "RZ": (cmath.exp(-0.5j * t), 0, 0, cmath.exp(0.5j * t)),
    }[name]


def apply(state: State, gate: Gate) -> State:
    """Return the new state after one bound gate."""
    out = list(state)
    if gate.name in {"CNOT", "CZ", "RZZ"}:
        a, b = (1 << q for q in gate.qubits)
        for i in range(len(out)):
            if gate.name == "CNOT" and i & a and not i & b:
                out[i], out[i | b] = state[i | b], state[i]
            elif gate.name == "CZ" and i & a and i & b:
                out[i] = -state[i]
            elif gate.name == "RZZ":
                parity = bool(i & a) ^ bool(i & b)
                out[i] = state[i] * cmath.exp((0.5j if parity else -0.5j) * float(gate.angle))
        return out
    m00, m01, m10, m11 = _one_qubit_matrix(gate)
    bit = 1 << gate.qubits[0]
    for i in range(len(out)):
        if not i & bit:
            x, y = state[i], state[i | bit]
            out[i], out[i | bit] = m00 * x + m01 * y, m10 * x + m11 * y
    return out


def run(circuit: Circuit, params: Sequence[float] = (), initial: State | None = None) -> State:
    state = initial if initial is not None else basis_state(circuit.n_qubits)
    for gate in circuit.bind(params).gates:
        state = apply(state, gate)
    return state


def inner(a: State, b: State) -> complex:
    return sum(x.conjugate() * y for x, y in zip(a, b))


def unitary_columns(circuit: Circuit) -> list[State]:
    """Column k is the circuit applied to basis state |k⟩."""
    return [run(circuit, initial=basis_state(circuit.n_qubits, k)) for k in range(2 ** circuit.n_qubits)]


def equivalent(a: Circuit, b: Circuit, tol: float = 1e-10) -> bool:
    """Full-unitary equality up to one global phase (not just on |0…0⟩)."""
    ua = [z for col in unitary_columns(a) for z in col]
    ub = [z for col in unitary_columns(b) for z in col]
    pivot = max(range(len(ub)), key=lambda i: abs(ub[i]))
    phase = ua[pivot] / ub[pivot]
    return abs(abs(phase) - 1) <= tol and all(abs(x - phase * y) <= tol for x, y in zip(ua, ub))


def apply_pauli(state: State, label: str) -> State:
    """P|ψ⟩ for a Pauli string such as "XZ" (right-most character acts on qubit 0)."""
    out = [0j] * len(state)
    ops = label[::-1]
    for i, amp in enumerate(state):
        j, phase = i, 1 + 0j
        for q, op in enumerate(ops):
            bit = (i >> q) & 1
            if op in "XY":
                j ^= 1 << q
            if op == "Y":
                phase *= 1j if bit == 0 else -1j
            elif op == "Z" and bit:
                phase = -phase
        out[j] += phase * amp
    return out


def expectation(state: State, observable: PauliSum) -> float:
    """⟨ψ|H|ψ⟩. Strings made only of I and Z are diagonal, so they need just |amplitude|²."""
    total = 0.0
    for c, p in observable:
        if set(p) <= {"I", "Z"}:
            mask = sum(1 << q for q, op in enumerate(p[::-1]) if op == "Z")
            total += c * sum(abs(a) ** 2 * (-1 if bin(i & mask).count("1") % 2 else 1) for i, a in enumerate(state))
        else:
            total += c * inner(state, apply_pauli(state, p)).real
    return total


def parameter_shift_gradient(circuit: Circuit, observable: PauliSum, params: Sequence[float]) -> list[float]:
    """Exact gradient of ⟨H⟩ for Pauli-rotation gates: shift each occurrence by ±π/2."""
    grad = [0.0] * len(params)
    bound = circuit.bind(params).gates
    for k, gate in enumerate(circuit.gates):
        if not isinstance(gate.angle, Param):
            continue
        def energy_with(delta: float) -> float:
            shifted = list(bound)
            shifted[k] = Gate(gate.name, gate.qubits, float(bound[k].angle) + delta)
            return expectation(run(Circuit(circuit.n_qubits, tuple(shifted))), observable)
        grad[gate.angle.index] += gate.angle.scale * (energy_with(math.pi / 2) - energy_with(-math.pi / 2)) / 2
    return grad


def minimize(circuit: Circuit, observable: PauliSum, start: Sequence[float], learning_rate: float = 0.2,
             max_steps: int = 1000, tolerance: float = 1e-9) -> tuple[list[float], float]:
    """Deterministic gradient descent on ⟨H⟩; stops once every |∂⟨H⟩/∂θ| < tolerance."""
    params = list(start)
    for _ in range(max_steps):
        grad = parameter_shift_gradient(circuit, observable, params)
        if max(abs(g) for g in grad) < tolerance:
            break
        params = [p - learning_rate * g for p, g in zip(params, grad)]
    return params, expectation(run(circuit, params), observable)


def exact_ground_energy(observable: PauliSum, n_qubits: int) -> float:
    """Smallest eigenvalue of the Pauli sum by Jacobi diagonalisation of its matrix.

    A complex Hermitian H = A + iB is embedded as the real symmetric [[A, -B], [B, A]],
    which has the same eigenvalues (each one twice).
    """
    h = observable_matrix(observable, n_qubits)
    a = [[z.real for z in row] for row in h]
    b = [[z.imag for z in row] for row in h]
    embedded = [ra + [-x for x in rb] for ra, rb in zip(a, b)] + [rb + ra for ra, rb in zip(a, b)]
    return min(_jacobi_eigenvalues(embedded))


def observable_matrix(observable: PauliSum, n_qubits: int) -> list[list[complex]]:
    """Dense matrix of a Pauli sum, built column by column from H|k⟩."""
    dim = 2 ** n_qubits
    columns = []
    for k in range(dim):
        column = [0j] * dim
        for c, label in observable:
            for i, z in enumerate(apply_pauli(basis_state(n_qubits, k), label)):
                column[i] += c * z
        columns.append(column)
    return [[columns[k][i] for k in range(dim)] for i in range(dim)]


def _jacobi_eigenvalues(m: list[list[float]], sweeps: int = 100) -> list[float]:
    n, m = len(m), [row[:] for row in m]
    for _ in range(sweeps):
        off = sum(m[i][j] ** 2 for i in range(n) for j in range(n) if i != j)
        if off < 1e-30:
            break
        for p, q in itertools.combinations(range(n), 2):
            if abs(m[p][q]) < 1e-300:
                continue
            theta = (m[q][q] - m[p][p]) / (2 * m[p][q])
            t = math.copysign(1.0, theta) / (abs(theta) + math.sqrt(theta * theta + 1))
            c = 1 / math.sqrt(t * t + 1)
            s = t * c
            for k in range(n):
                mkp, mkq = m[k][p], m[k][q]
                m[k][p], m[k][q] = c * mkp - s * mkq, s * mkp + c * mkq
            for k in range(n):
                mpk, mqk = m[p][k], m[q][k]
                m[p][k], m[q][k] = c * mpk - s * mqk, s * mpk + c * mqk
    return [m[i][i] for i in range(n)]


def _r(x: float) -> float:
    """Round for stable, readable reports."""
    return round(x, 10) + 0.0

# ──────────────────────── 1. quantum machine learning ────────────────────────


def feature_map(x: tuple[float, float]) -> Circuit:
    """Two-qubit ZZ-style feature map (Havlíček et al. 2019 pattern), one repetition."""
    x1, x2 = x
    return Circuit(2, (Gate("H", (0,)), Gate("H", (1,)), Gate("RZ", (0,), 2 * x1), Gate("RZ", (1,), 2 * x2),
                       Gate("CNOT", (0, 1)), Gate("RZ", (1,), 2 * (math.pi - x1) * (math.pi - x2)),
                       Gate("CNOT", (0, 1))))


def quantum_kernel(x: tuple[float, float], y: tuple[float, float]) -> float:
    return abs(inner(run(feature_map(x)), run(feature_map(y)))) ** 2


def rbf_kernel(x: tuple[float, float], y: tuple[float, float], gamma: float = 1.0) -> float:
    return math.exp(-gamma * ((x[0] - y[0]) ** 2 + (x[1] - y[1]) ** 2))


Point = tuple[float, float]
Kernel = Callable[[Point, Point], float]


def nearest_centroid_accuracy(kernel: Kernel, train: list[tuple[Point, int]], test: list[tuple[Point, int]]) -> float:
    """Classify by squared feature-space distance to each class mean (needs only the kernel)."""
    classes = sorted({label for _, label in train})
    groups = {c: [x for x, label in train if label == c] for c in classes}
    self_term = {c: sum(kernel(a, b) for a in g for b in g) / len(g) ** 2 for c, g in groups.items()}
    def predict(x: Point) -> int:
        return min(classes, key=lambda c: self_term[c] - 2 * sum(kernel(x, a) for a in groups[c]) / len(groups[c]))
    return sum(predict(x) == label for x, label in test) / len(test)


def quantum_machine_learning(seed: int = 7) -> dict:
    rng = random.Random(seed)
    points = [(rng.uniform(0, math.pi), rng.uniform(0, math.pi)) for _ in range(64)]
    data = [(p, int(math.sin(p[0]) * math.cos(p[1]) > 0)) for p in points]  # invented labels
    train, test = data[:32], data[32:]
    return {
        "task": "binary classification of 64 invented 2-D points (32 train / 32 test), label = [sin(x1)*cos(x2) > 0]",
        "classifier": "kernel nearest-centroid (no fitted weights)",
        "quantum_kernel_test_accuracy": _r(nearest_centroid_accuracy(quantum_kernel, train, test)),
        "classical_rbf_test_accuracy": _r(nearest_centroid_accuracy(rbf_kernel, train, test)),
        "rbf_gamma": 1.0,
        "note": "gamma fixed in advance, not tuned; a toy comparison, not a benchmark",
    }

# ───────────────────────── 2. quantum optimisation ─────────────────────────


GRAPH = ((0, 1), (1, 2), (2, 3), (3, 4), (4, 0), (0, 2))  # 5-node ring plus one chord


def cut_value(bits: int, edges: Iterable[tuple[int, int]] = GRAPH) -> int:
    return sum(((bits >> i) ^ (bits >> j)) & 1 for i, j in edges)


def maxcut_cost(n: int, edges=GRAPH) -> PauliSum:
    """−C with C = Σ (1 − Z_i Z_j)/2, so minimising ⟨−C⟩ maximises the expected cut."""
    def zz(i: int, j: int) -> str:
        return "".join("Z" if q in (i, j) else "I" for q in reversed(range(n)))
    return tuple((c, p) for i, j in edges for c, p in ((-0.5, "I" * n), (0.5, zz(i, j))))


def qaoa_circuit(n: int, p: int, edges=GRAPH) -> Circuit:
    """Parameters [γ1, β1, γ2, β2, …]: e^{-iγC} = Π RZZ(−γ) up to phase, e^{-iβΣX} = Π RX(2β)."""
    gates = [Gate("H", (q,)) for q in range(n)]
    for layer in range(p):
        gates += [Gate("RZZ", (i, j), Param(2 * layer, -1.0)) for i, j in edges]
        gates += [Gate("RX", (q,), Param(2 * layer + 1, 2.0)) for q in range(n)]
    return Circuit(n, tuple(gates))


def quantum_optimisation(n: int = 5) -> dict:
    cost = maxcut_cost(n)
    best = max(cut_value(b) for b in range(2 ** n))
    grid = [(g * math.pi / 16, b * math.pi / 32) for g in range(16) for b in range(16)]
    seed_point = min(grid, key=lambda gb: expectation(run(qaoa_circuit(n, 1), gb), cost))
    layers, start = {}, list(seed_point)
    for p in (1, 2):
        params, energy = minimize(qaoa_circuit(n, p), cost, start, learning_rate=0.05)
        probs = [abs(a) ** 2 for a in run(qaoa_circuit(n, p), params)]
        layers[f"p={p}"] = {
            "expected_cut": _r(-energy),
            "approximation_ratio": _r(-energy / best),
            "probability_of_an_optimal_cut": _r(sum(pr for b, pr in enumerate(probs) if cut_value(b) == best)),
        }
        start = params + params[-2:]
    return {
        "problem": f"MaxCut on {n} nodes, edges {list(GRAPH)}",
        "exact_max_cut_brute_force": best,
        "random_assignment_expected_cut": len(GRAPH) / 2,
        "qaoa": layers,
        "optimiser": "16x16 grid for p=1, then parameter-shift gradient descent; p=2 starts from p=1",
    }

# ─────────────────── 3. circuit synthesis & compilation ───────────────────


def _wire_key(gate: Gate) -> tuple[int, ...]:
    """Qubits as an identity key: CNOT is directional, every other gate here is symmetric."""
    return gate.qubits if gate.name == "CNOT" else tuple(sorted(gate.qubits))


def compile_circuit(circuit: Circuit) -> Circuit:
    """One pass: cancel adjacent self-inverse pairs and merge adjacent same-axis rotations.

    "Adjacent" means no gate in between touches those qubits. Rotations whose merged
    angle is a multiple of 2π are dropped (they equal −I, a global phase).
    """
    out: list[Gate] = []
    for gate in circuit.gates:
        key = _wire_key(gate)
        last = next((k for k in range(len(out) - 1, -1, -1) if set(out[k].qubits) & set(gate.qubits)), None)
        prev = out[last] if last is not None else None
        if prev is not None and prev.name == gate.name and _wire_key(prev) == key:
            if gate.name in SELF_INVERSE:
                out.pop(last)
                continue
            if gate.name in ROTATIONS:
                angle = float(prev.angle) + float(gate.angle)
                out.pop(last)
                if abs(math.remainder(angle, 2 * math.pi)) > 1e-12:
                    out.insert(last, Gate(gate.name, key, angle))
                continue
        out.append(Gate(gate.name, key, gate.angle))
    return Circuit(circuit.n_qubits, tuple(out))


DEMO_CIRCUIT = Circuit(2, (
    Gate("H", (0,)), Gate("CNOT", (0, 1)), Gate("CNOT", (0, 1)), Gate("H", (0,)),
    Gate("RZ", (1,), 0.25), Gate("RX", (0,), 0.5), Gate("RZ", (1,), 0.5), Gate("RX", (0,), -0.5),
    Gate("X", (1,)), Gate("X", (1,)), Gate("RZZ", (0, 1), 0.7), Gate("RZZ", (1, 0), -0.2),
    Gate("RY", (0,), math.pi), Gate("RY", (0,), math.pi), Gate("CZ", (0, 1)),
))


def circuit_compilation() -> dict:
    compiled = compile_circuit(DEMO_CIRCUIT)
    faulty = Circuit(2, compiled.gates[:-1])  # deliberately drop one gate
    return {
        "gate_count_before": len(DEMO_CIRCUIT.gates),
        "gate_count_after": len(compiled.gates),
        "compiled": [g.name + str(list(g.qubits)) + ("" if g.angle is None else f"({_r(float(g.angle))})")
                     for g in compiled.gates],
        "full_unitary_equivalent_up_to_global_phase": equivalent(DEMO_CIRCUIT, compiled),
        "sanity_check_faulty_compile_detected": not equivalent(DEMO_CIRCUIT, faulty),
    }

# ─────────────────────────────── 4. chemical AI ───────────────────────────────

# H2 at 0.735 Å, 2-qubit Pauli form, coefficients copied verbatim from the Qiskit tutorials
# ("originally computed by Qiskit Nature for an H2 molecule"), e.g.
#   https://qiskit-community.github.io/qiskit-algorithms/tutorials/02_vqe_advanced_options.html
#   (also qiskit-tutorials 01_algorithms_introduction.ipynb). Reference eigenvalue there: -1.85728.
# Labels use Qiskit order (right-most character = qubit 0). Units: hartree, as in the source.
H2_HAMILTONIAN: PauliSum = (
    (-1.052373245772859, "II"),
    (0.39793742484318045, "IZ"),
    (-0.39793742484318045, "ZI"),
    (-0.01128010425623538, "ZZ"),
    (0.18093119978423156, "XX"),
)

# One-parameter ansatz cos(θ/2)|01⟩ + sin(θ/2)|10⟩. θ = 0 is the reference state |01⟩, and the
# ground state lies in this two-state sector because XX couples |01⟩ and |10⟩.
H2_ANSATZ = Circuit(2, (Gate("RY", (1,), Param(0)), Gate("CNOT", (1, 0)), Gate("X", (0,))))


def chemical_ai() -> dict:
    exact = exact_ground_energy(H2_HAMILTONIAN, 2)
    reference = expectation(run(H2_ANSATZ, [0.0]), H2_HAMILTONIAN)
    (theta,), energy = minimize(H2_ANSATZ, H2_HAMILTONIAN, [0.0], learning_rate=0.5)
    return {
        "system": "H2 at 0.735 Å, 2-qubit Hamiltonian (5 Pauli terms) from the Qiskit tutorials",
        "source": "https://qiskit-community.github.io/qiskit-algorithms/tutorials/02_vqe_advanced_options.html",
        "reference_state_energy": _r(reference),
        "vqe_energy": _r(energy),
        "exact_ground_energy": _r(exact),
        "abs_error": float(f"{abs(energy - exact):.3e}"),
        "vqe_theta": _r(theta),
        "units": "hartree (as given by the source operator)",
    }

# ─────────────────────────────── entry point ───────────────────────────────


def run_intersection() -> dict:
    return {
        "title": "AI ∩ Quantum Computing: one statevector core, four applications",
        "disclaimer": DISCLAIMER,
        "core": "n-qubit statevector · H X Z RX RY RZ CNOT CZ RZZ · Pauli sums · parameter-shift descent",
        "quantum_machine_learning": quantum_machine_learning(),
        "quantum_optimisation": quantum_optimisation(),
        "circuit_synthesis_compilation": circuit_compilation(),
        "chemical_ai": chemical_ai(),
    }


def render_summary(report: dict) -> str:
    q, o, c, h = (report[k] for k in ("quantum_machine_learning", "quantum_optimisation",
                                      "circuit_synthesis_compilation", "chemical_ai"))
    rows = [
        ("Quantum ML", f"quantum-kernel accuracy {q['quantum_kernel_test_accuracy']:.3f}  ·  "
                       f"classical RBF {q['classical_rbf_test_accuracy']:.3f}"),
        ("Optimisation", f"MaxCut max {o['exact_max_cut_brute_force']}  ·  QAOA p=1 ratio "
                         f"{o['qaoa']['p=1']['approximation_ratio']:.3f}  ·  p=2 ratio {o['qaoa']['p=2']['approximation_ratio']:.3f}"),
        ("Compilation", f"{c['gate_count_before']} → {c['gate_count_after']} gates  ·  unitary-equivalent "
                        f"{c['full_unitary_equivalent_up_to_global_phase']}  ·  faulty compile caught "
                        f"{c['sanity_check_faulty_compile_detected']}"),
        ("Chemical AI", f"H2 VQE {h['vqe_energy']:.8f} Ha  ·  exact {h['exact_ground_energy']:.8f} Ha  ·  "
                        f"|Δ| {h['abs_error']:.1e}"),
    ]
    width = max(len(text) for _, text in rows) + 17
    line = "─" * width
    body = "\n".join(f"  {name:<13}│ {text}" for name, text in rows)
    note = textwrap.fill(report["disclaimer"], width - 4, initial_indent="  ", subsequent_indent="  ")
    return f"{line}\n  {report['title']}\n  core: {report['core']}\n{line}\n{body}\n{line}\n{note}\n{line}\n"


if __name__ == "__main__":  # standalone use: python3 intersection.py
    print(render_summary(run_intersection()), end="")
