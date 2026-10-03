"""Tests for src/mqoes/intersection.py: core identities and the four thin applications."""
from __future__ import annotations

import ast
import contextlib
import io
import math
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from mqoes import intersection as ix  # noqa: E402
from mqoes.cli import main, render_json  # noqa: E402
from mqoes.intersection import Circuit, Gate, Param  # noqa: E402


def c1(*gates: Gate) -> Circuit:
    return Circuit(1, gates)


def c2(*gates: Gate) -> Circuit:
    return Circuit(2, gates)


class CoreIdentityTests(unittest.TestCase):
    def test_single_qubit_identities(self):
        H, X, Z = Gate("H", (0,)), Gate("X", (0,)), Gate("Z", (0,))
        self.assertTrue(ix.equivalent(c1(H, H), c1()))
        self.assertTrue(ix.equivalent(c1(X, X), c1()))
        self.assertTrue(ix.equivalent(c1(H, X, H), c1(Z)))
        self.assertTrue(ix.equivalent(c1(Gate("RZ", (0,), 0.3), Gate("RZ", (0,), 0.4)), c1(Gate("RZ", (0,), 0.7))))
        self.assertTrue(ix.equivalent(c1(Gate("RY", (0,), 2 * math.pi)), c1()))  # -I is a global phase
        self.assertTrue(ix.equivalent(c1(Gate("RZ", (0,), math.pi)), c1(Z)))     # up to phase

    def test_two_qubit_identities(self):
        cnot, h1 = Gate("CNOT", (0, 1)), Gate("H", (1,))
        self.assertTrue(ix.equivalent(c2(cnot, cnot), c2()))
        self.assertTrue(ix.equivalent(c2(h1, cnot, h1), c2(Gate("CZ", (0, 1)))))
        self.assertTrue(ix.equivalent(c2(Gate("RZZ", (0, 1), 0.9)),
                                      c2(cnot, Gate("RZ", (1,), 0.9), cnot)))
        self.assertFalse(ix.equivalent(c2(Gate("CNOT", (0, 1))), c2(Gate("CNOT", (1, 0)))))

    def test_equivalence_checks_full_unitary_not_just_zero_state(self):
        # RZ only adds a phase to |0>, so a |0>-only check would wrongly accept this.
        self.assertFalse(ix.equivalent(c1(Gate("RZ", (0,), 0.4), Gate("H", (0,))), c1(Gate("H", (0,)))))

    def test_pauli_expectations(self):
        bell = ix.run(c2(Gate("H", (0,)), Gate("CNOT", (0, 1))))
        for label, value in (("XX", 1.0), ("ZZ", 1.0), ("YY", -1.0), ("ZI", 0.0)):
            self.assertAlmostEqual(ix.expectation(bell, ((1.0, label),)), value, places=12)
        plus_i = ix.run(c1(Gate("RX", (0,), -math.pi / 2)))  # (|0> + i|1>)/sqrt(2)
        self.assertAlmostEqual(ix.expectation(plus_i, ((1.0, "Y"),)), 1.0, places=12)

    def test_parameter_shift_matches_finite_difference(self):
        circuit = c2(Gate("RY", (0,), Param(0)), Gate("RZZ", (0, 1), Param(1, -1.0)),
                     Gate("RX", (1,), Param(1, 2.0)), Gate("RX", (0,), Param(0, 0.5)))
        obs = ((0.7, "XZ"), (-0.3, "YY"), (0.2, "IZ"))
        params, eps = [0.37, -1.1], 1e-6
        grad = ix.parameter_shift_gradient(circuit, obs, params)
        for i in range(2):
            up, down = list(params), list(params)
            up[i] += eps
            down[i] -= eps
            fd = (ix.expectation(ix.run(circuit, up), obs) - ix.expectation(ix.run(circuit, down), obs)) / (2 * eps)
            self.assertAlmostEqual(grad[i], fd, places=7)

    def test_exact_ground_energy_handles_complex_hermitian(self):
        self.assertAlmostEqual(ix.exact_ground_energy(((1.0, "Y"),), 1), -1.0, places=12)
        self.assertAlmostEqual(ix.exact_ground_energy(((1.0, "XY"), (0.5, "ZI")), 2), -math.sqrt(1.25), places=12)


class ApplicationTests(unittest.TestCase):
    def test_vqe_matches_exact_eigenvalue(self):
        result = ix.chemical_ai()
        self.assertLess(abs(result["vqe_energy"] - result["exact_ground_energy"]), 1e-6)
        # closed form for the {|01>,|10>} block of the same operator
        c = dict((p, v) for v, p in ix.H2_HAMILTONIAN)
        d01 = c["II"] - c["IZ"] + c["ZI"] - c["ZZ"]
        d10 = c["II"] + c["IZ"] - c["ZI"] - c["ZZ"]
        closed = (d01 + d10) / 2 - math.sqrt(((d01 - d10) / 2) ** 2 + c["XX"] ** 2)
        self.assertAlmostEqual(result["exact_ground_energy"], closed, places=9)
        self.assertAlmostEqual(result["exact_ground_energy"], -1.85728, places=5)  # source's reference value
        self.assertLess(result["vqe_energy"], result["reference_state_energy"])

    def test_qaoa_against_brute_force(self):
        cost = ix.maxcut_cost(5)
        for bits in range(32):  # the cost observable is exactly -cut on basis states
            self.assertAlmostEqual(ix.expectation(ix.basis_state(5, bits), cost), -ix.cut_value(bits), places=12)
        result = ix.quantum_optimisation()
        best = result["exact_max_cut_brute_force"]
        self.assertEqual(best, max(ix.cut_value(b) for b in range(32)))
        p1, p2 = result["qaoa"]["p=1"], result["qaoa"]["p=2"]
        self.assertLessEqual(p2["expected_cut"], best)
        self.assertGreater(p1["expected_cut"], result["random_assignment_expected_cut"])
        self.assertGreaterEqual(p2["approximation_ratio"], p1["approximation_ratio"])

    def test_compilation_reduces_and_preserves_unitary(self):
        result = ix.circuit_compilation()
        self.assertEqual((result["gate_count_before"], result["gate_count_after"]), (15, 3))
        self.assertTrue(result["full_unitary_equivalent_up_to_global_phase"])
        self.assertTrue(result["sanity_check_faulty_compile_detected"])

    def test_quantum_kernel_is_a_valid_fidelity(self):
        x, y = (0.3, 1.2), (2.0, 0.4)
        self.assertAlmostEqual(ix.quantum_kernel(x, x), 1.0, places=12)
        self.assertAlmostEqual(ix.quantum_kernel(x, y), ix.quantum_kernel(y, x), places=12)
        self.assertTrue(0.0 <= ix.quantum_kernel(x, y) <= 1.0)


class DeterminismAndIsolationTests(unittest.TestCase):
    def test_report_is_deterministic_and_matches_shipped_file(self):
        shipped = (ROOT / "reports" / "intersection.json").read_bytes()
        self.assertEqual(render_json(ix.run_intersection()).encode("utf-8"), shipped)
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "intersection.json"
            with contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(main(["intersection", "--output", str(target)]), 0)
            self.assertEqual(target.read_bytes(), shipped)
        self.assertIn("not evidence of quantum advantage", out.getvalue().replace("\n  ", " "))

    def test_intersection_is_isolated_from_the_detector_path(self):
        def imports(path: Path) -> set[str]:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            names = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    names.add(("." * node.level) + (node.module or ""))
                elif isinstance(node, ast.Import):
                    names.update(alias.name for alias in node.names)
            return names
        src = ROOT / "src" / "mqoes"
        self.assertFalse({n for n in imports(src / "intersection.py") if n.startswith(".") or "mqoes" in n})
        for detector_module in ("triage.py", "contracts.py", "stream32.py", "quantum_lab.py"):
            self.assertFalse({n for n in imports(src / detector_module) if "intersection" in n}, detector_module)
        self.assertNotIn("intersection", (src / "triage.py").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
