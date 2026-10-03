# CHANGES (post-reconstruction fixes, 2026-10-02)

Baseline: the files as shipped in the Quick Share download, plus the reconstruction-only
`run.py` shim (the pristine copy is kept outside this repository). Standard library
only. All shipped deterministic outputs (`reports/*.json`) still reproduce byte-for-byte.

1. **Capsule validator overflow** (`failure_capsules.py`): `math.isfinite()` raised
   `OverflowError` on JSON integers too large for a float (e.g. 400 digits). New helper
   `_is_finite_number()` treats them as non-finite, so `validate_capsule()` reports
   "`<field>` must be finite" and `generate_replay()` raises `ValueError`. The CLI
   (`src/mqoes/cli.py`) now also catches `OverflowError` and exits with status 2 instead of
   printing a traceback.
2. **Circuit equivalence** (`src/mqoes/quantum_lab.py`): the demo compared only the output
   states from |0>, where RZ is a pure phase, so a dropped or wrong-angle RZ still passed.
   New `circuit_unitary()` / `circuits_equivalent_up_to_global_phase()` compare the full 2x2
   unitary (images of |0> and |1>) up to a single shared global phase. The demo's result is
   unchanged (still `true`), so `reports/quantum-toys.json` is byte-identical.
3. **Test import path** (`tests/test_prototype.py`, `tests/test_stream32.py`): these now add the
   project root as well as `src/`, because `mqoes.cli` imports the top-level `failure_capsules`
   and `outage_radar` modules. The suite now passes from any working directory.
4. **Loopback and Host enforcement** (`src/mqoes/cli.py`): the new `_make_server()` refuses any
   host other than `127.0.0.1`, even when called directly. Every request must carry exactly one
   Host header equal to `127.0.0.1:<bound port>` or `localhost:<bound port>` (case-insensitive).
   A missing Host header gets 400; any other Host gets 403. This blocks DNS-rebinding access.
5. **Request timeout** (`src/mqoes/cli.py`): the handler sets a socket `timeout`
   (`REQUEST_TIMEOUT_SECONDS = 15`). A stalled body read returns 408 and closes the connection.
   A body shorter than its Content-Length returns 400.
6. **stream32 reset clamp: behavior deliberately NOT changed.** See `RECONSTRUCTION_NOTES.md`. The
   unclamped reset matches the HLS source in a separate `oes32-triage-hls-v0.1.0.zip` archive. A separate
   semantic difference is documented there: hardware checks reset on every packet, while the model
   applies it once.
7. **Minor fixes:**
   - `src/mqoes/contracts.py`: replay lines are split on `\n` only, and a trailing `\r` is
     stripped (CRLF accepted). Previously `str.splitlines()` also split on U+2028/U+2029,
     VT and FF, which are legal unescaped inside JSON strings.
   - `outage_radar.py`: removed two checks that could never trigger. Services must be
     non-empty, and `_validate_checks()` already rejects any check that references an
     undeclared service or dependency.
   - `src/mqoes/cli.py`: HTTP 500 responses now return the generic
     `{"error":"internal server error"}`; only the exception type is logged locally. HTTP 400
     still returns the project's own `ValidationError` messages, which the UI shows. Other
     `ValueError`/`OverflowError` cases return a generic `{"error":"invalid input"}`.

New tests are in `tests/test_regressions.py` (18 tests). The full suite went from 34 to 52 tests.

## Stress replay experiment (2026-10-02)
Added; no existing data, prereg or report was changed. The original 52 tests and the shipped
outputs are unchanged.
- `tools/generate_stress.py`: seeded, stdlib-only generator. It writes `data/stress_replay.jsonl`
  (held-out test, seed 20261003) and `data/stress_calibration.jsonl` (calibration, seed
  20261004); `--check` verifies the files.
- `prereg/stress_locked.json`: fixed 0.50/0.50, locked before evaluation.
- `tools/calibrate_stress.py`: chooses thresholds on the calibration split only (pooled no-event
  FP rate <= 5%). It writes `prereg/stress_calibrated_locked.json` (0.7436 / 1.4265) and
  `reports/stress-calibration.json`.
- `prereg/STRESS_LOCK_MANIFEST.json`: sha256 of the data, preregs, tools and calibration record,
  recorded before evaluation.
- `reports/stress-report-fixed-0.50.json` and `reports/stress-report-calibrated.json`: outputs of
  `run.py evaluate`.
- `docs/STRESS_RESULTS.md`: per-regime comparison and verdict. The result is **negative**: no
  advantage for the block score.
- `tests/test_stress.py`: 8 tests covering generator determinism, file validity and structure,
  manifest hashes, untuned fixed prereg, calibration reproducibility from the calibration split
  only, byte-identical report regeneration, and the score <= peak bound. The suite is now 60 tests.

## AI ∩ Quantum intersection module (2026-10-02)
Added; nothing existing was replaced. `quantum_lab.py` is unchanged, and all earlier reports
reproduce byte-for-byte.
- `src/mqoes/intersection.py`: one stdlib statevector core (gates, Pauli sums, parameter-shift
  descent, full-unitary equivalence, Jacobi exact diagonalisation) and four short applications:
  quantum-kernel ML vs RBF, QAOA MaxCut vs brute force, a compile pass with unitary
  verification, and H2 VQE vs exact. The H2 coefficients come from the Qiskit tutorials (cited).
- `src/mqoes/cli.py`: new `intersection` subcommand. It prints a summary and writes
  `reports/intersection.json` (`--output -` skips the file).
- `reports/intersection.json`: deterministic report.
- `docs/INTERSECTION.md`: explainer with the diagram mapping, a mermaid diagram, results, source
  and limits.
- `tests/test_intersection.py`: 12 tests covering gate identities, full-unitary equivalence,
  Pauli expectations, parameter shift vs finite differences, complex-Hermitian diagonalisation,
  VQE within 1e-6 of exact (and of a closed form), QAOA vs brute force, compilation,
  kernel validity, determinism, and isolation from the detector. The suite is now 72 tests.

## Python 3.11 compatibility fix (2026-10-02)
- `tools/generate_stress.py`: moved `data.count(b"\n")` out of an f-string expression in the
  progress message. A backslash inside an f-string expression needs Python 3.12+, so the first CI
  run failed on 3.11. This is a code-only edit: the generated stress files are byte-identical on
  3.11, 3.12 and 3.13.
- `prereg/STRESS_LOCK_MANIFEST.json`: the original pre-evaluation `sha256` map is unchanged. A dated
  `amendments` entry records the generator's pre-evaluation hash, its new hash, the reason and the
  verification.
- `tests/test_stress.py`: data, preregistration and report hashes are still checked strictly. A
  tool file may differ only through a documented code-only amendment that chains from the
  recorded hash.
- `src/mqoes/quantum_lab.py`: the kernel-classifier similarity totals now use `math.fsum` instead of
  the built-in `sum()`. Python 3.12 changed float `sum()` to compensated summation, so 3.11 gave a
  last-digit difference in one value of `reports/quantum-toys.json`. `math.fsum` is correctly
  rounded on every version; the committed report is unchanged and now reproduces byte for byte on
  3.11, 3.12 and 3.13.

## 0.1.1 (2026-10-03): metadata and documentation only
No code, data, preregistration or report changed; all reports still reproduce byte for byte.
- `.zenodo.json`: a fuller description (what is included, negative results, limits), 15 keywords,
  `language: eng`, the licence id `agpl-3.0-only` from Zenodo's vocabulary, a `notes` field, and
  related identifiers: the repository (`isSupplementTo`), plus `references` to the Qiskit
  Algorithms tutorial (H2 coefficients), oes32-hls (10.5281/zenodo.22985525), oes-resilience
  (10.5281/zenodo.23071166) and oes32-residual. `references` is used because Zenodo's relation
  vocabulary has no `isRelatedTo`.
- `README.md`: a Related work section explains that oes32-hls holds the reference C++ triage
  kernel (v2) and that `stream32` models the earlier v1 logic and differs from it. A How to cite
  section gives APA and BibTeX for the concept DOI. A stale sentence that called `quantum_lab.py`
  "unchanged" now points to its listed edits.
- `CITATION.cff`: version 0.1.1, dated 2026-10-03, with updated keywords and an oes32-hls
  reference.
- After release: v0.1.1 was archived on Zenodo as version DOI 10.5281/zenodo.23117526 (concept
  DOI 10.5281/zenodo.23113851). It was added to `CITATION.cff` and `README.md`, with no further release.
