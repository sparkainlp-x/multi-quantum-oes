# Multi-Quantum OES

**An offline, standard-library Python workbench** for explainable OES replay triage, separate OES32 checks, saved outage snapshots, metadata-only failure capsules, and an isolated "AI ∩ quantum" toy lab. The quantum experiments never enter the telemetry scoring path.

[![tests](https://github.com/sparkainlp-x/multi-quantum-oes/actions/workflows/tests.yml/badge.svg)](https://github.com/sparkainlp-x/multi-quantum-oes/actions/workflows/tests.yml)
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](LICENSE)
[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.23113851.svg)](https://doi.org/10.5281/zenodo.23113851)
[![Python 3.11–3.13](https://img.shields.io/badge/python-3.11%E2%80%933.13-blue.svg)](.github/workflows/tests.yml)
[![Status: research prototype](https://img.shields.io/badge/status-research%20prototype-orange.svg)](#what-it-is-not)

Version 0.1.0 was the first public release; 0.1.1 updates metadata and documentation only. The release packages the original offline prototype together with:

- **hardening fixes,** each with a regression test (see [CHANGES.md](CHANGES.md));
- **a preregistered [stress evaluation](#stress-evaluation),** whose result is negative: the block score does not beat a simple max-abs baseline;
- **a single-module [AI ∩ Quantum](#ai--quantum-intersection-module) demo,** in which one exact statevector core powers four toy applications.

Python 3.11 or newer is required. No third-party packages and no network access are needed. Everything bundled is synthetic.

## What it is

- **OES-512 replay triage.** Each timestamped frame has exactly 512 finite values, split into sixteen 32-value blocks.
  - **Block score:** `0.45·max|x| + 0.35·RMS(x) + 0.20·mean|x|`. A block is flagged when the score is `>=` the candidate threshold.
  - **What is reported:** a separate max-abs baseline, event recall and misses, false-positive counts and rates, annotated block precision/recall/IoU, latency, ranked block evidence, and the SHA-256 of the exact replay and protocol bytes.
  - **What it does not do:** temporal smoothing, adaptive thresholding or rate limiting.
- **Preregistered thresholds.** Thresholds are read from a preregistration file and never tuned by the CLI. `prereg/example_locked.json` is the supplied example, with both thresholds at 0.50. It is uncalibrated and not a plant recommendation.
- **OES32 adaptive-tau stream model.** A float-level reference path for the `oes32_triage.cpp` branch logic.
  - For each packet: `weighted = data_value × adaptive_tau`. A shock flag diverts immediately; otherwise a packet is diverted only when `weighted > rate_limit_threshold`.
  - The per-packet state updates clamp tau to [0.05, 4.0].
  - It is not a bit-exact HLS/FPGA emulator. See [RECONSTRUCTION_NOTES.md](RECONSTRUCTION_NOTES.md) for how its reset handling differs from the HLS source.
- **OES32 residual comparison.** Two arrays of exactly 32 values. The residual is `R = max|observed[i] − reference[i]|`, and the check fails only when `R > tolerance`, so equality passes.
- **Offline outage snapshot.** Classifies a saved JSON snapshot of service/dependency checks across sites by observed scope and quorum. It never probes hosts, and correlated failures are not treated as proof of cause.
- **Failure capsules.** Validates a versioned, metadata-only reproducibility capsule. It rejects known raw-measurement key names, but it is not a privacy scanner or certified anonymisation.
- **A local web interface** (`serve`) for all of the above. It binds to loopback only, checks the Host header, and processes uploads in memory without writing them to disk.

## What it is not

- **Not quantum computing.** The quantum modules are ordinary local classical calculations. They are not QPU runs, not trained or validated anomaly detection, and not evidence of quantum advantage.
- **Not an operational system.** It is not an alarm, safety or medical device, facility connector, process-control function or hardware validation.
- **Not evidence about real telemetry.** Every bundled replay is invented. The supplied `PilotTrace.pdf` (not included) describes a data contract; it contains no labelled capture, and no real telemetry values were imported or inferred from it.
- **Not connected to anything.** No data is sent to external services and no live system is contacted.

## Quick start

```bash
git clone https://github.com/sparkainlp-x/multi-quantum-oes.git
cd multi-quantum-oes
python3 run.py demo                              # rewrites reports/synthetic-report.json (byte-identical)
python3 run.py serve --host 127.0.0.1 --port 8877
```

Then open http://127.0.0.1:8877. The server refuses any host other than `127.0.0.1` and answers only requests whose Host header is `127.0.0.1:<port>` or `localhost:<port>`.

## Commands

```bash
python3 run.py evaluate --input data/synthetic_replay.jsonl \
  --prereg prereg/example_locked.json --output reports/synthetic-report.json
python3 run.py residual --input path/to/residual.json      # {"observed": [32], "reference": [32], "tolerance": t}
python3 run.py stream32 --input data/oes32_stream_synthetic.json
python3 run.py outage   --input data/outage_snapshot_synthetic.json
python3 run.py capsule  --input data/example_failure_capsule.json
python3 run.py quantum  --output reports/quantum-toys.json
python3 run.py intersection                                # prints a summary, writes reports/intersection.json
python3 tools/generate_synthetic.py                        # regenerates the bundled replay (seed 20261002)
python3 tools/generate_stress.py --check                   # verifies the stress replays match their generator
python3 -m unittest discover -s tests -v
```

**Replay input** is UTF-8 JSON Lines, one frame per line (LF or CRLF).

- **Required keys:**
  - `timestamp`: timezone-aware ISO-8601;
  - `channels`: 512 finite JSON numbers;
  - `regime`: a non-empty string;
  - `event_label`: exactly `"none"` for no event;
  - `event_id`: `null` for no event, otherwise a contiguous episode ID.
- **Optional keys:**
  - `affected_blocks`: unique block numbers 0–15; `null` or omitted means localisation is unknown;
  - incumbent flags/alarm: if used, must be supplied consistently on every frame.
- **Rejected:** duplicate keys, blank lines, non-finite values, booleans, unknown keys, non-increasing timestamps and malformed event episodes.

Reports are deterministic for identical input bytes and protocol. A preregistration's `locked_at` is self-declared and hashed; the software cannot prove when a protocol was frozen.

## Bundled synthetic replay

`data/synthetic_replay.jsonl` was generated locally with seed 20261002 by `tools/generate_synthetic.py`, which reproduces it byte for byte (sha256 `73984523…`).

- **Content:** 80 frames across four regimes (stable, noisy, localised burst and global shock), with two labelled event episodes.
- **Result:** under `prereg/example_locked.json` both events are detected with block IoU 1.0.
- **Reproducibility:** `reports/synthetic-report.json` reproduces byte for byte.

## Stress evaluation

The question was whether the block score beats the per-block max-abs baseline on a harder synthetic replay. Full method and tables: [docs/STRESS_RESULTS.md](docs/STRESS_RESULTS.md).

**Data.** `tools/generate_stress.py` writes two splits, each with 320 frames, six regimes and 11 labelled events:

- a held-out test split, seed 20261003;
- a separate calibration split, seed 20261004.

The regimes are: stable controls, heavy-tailed noise with isolated spikes, single-channel spike artifacts labelled `none`, weak multi-channel bursts, sustained drift, and sparse 3-channel spike events.

**Preregistration.** Two protocols were locked, and their hashes recorded in `prereg/STRESS_LOCK_MANIFEST.json`, before the test split was evaluated:

1. **Fixed:** 0.50 for both detectors.
2. **Calibrated:** thresholds chosen on the calibration split only, at a 5% no-event frame FP rate. This gave candidate 0.7436 and baseline 1.4265.

| protocol | detector | events detected | FP frames | FP episodes | block TP/FP/FN |
|---|---|---|---|---|---|
| fixed 0.50 | block score | 5/11 | 45/272 | 26 | 17/51/53 |
| fixed 0.50 | max-abs baseline | 8/11 | 81/272 | 14 | 23/146/47 |
| calibrated | block score | 0/11 | 17/272 | 14 | 0/17/70 |
| calibrated | max-abs baseline | 0/11 | 17/272 | 14 | 0/17/70 |

**Verdict: no demonstrated advantage.**

- **Fixed thresholds.** Because `score ≤ peak`, the block score can only flag a subset of the baseline's blocks at a shared threshold. At 0.50 it is simply more conservative: fewer events found and fewer FP frames, but more FP episodes.
- **Calibrated thresholds.** Both thresholds were driven by isolated spikes, so the two detectors behaved identically and missed every event.
- **Sample size.** With 11 invented events, none of these differences is statistically meaningful.

## AI ∩ Quantum intersection module

[`src/mqoes/intersection.py`](src/mqoes/intersection.py) is one standard-library module. A single exact statevector core (gates, Pauli-sum observables, parameter-shift gradient descent and full-unitary equivalence) powers four thin applications, matching the centre of the familiar "AI ∩ Quantum Computing" diagram. Explainer: [docs/INTERSECTION.md](docs/INTERSECTION.md).

```
$ python3 run.py intersection
  Quantum ML   │ quantum-kernel accuracy 0.531  ·  classical RBF 0.906
  Optimisation │ MaxCut max 5  ·  QAOA p=1 ratio 0.822  ·  p=2 ratio 0.925
  Compilation  │ 15 → 3 gates  ·  unitary-equivalent True  ·  faulty compile caught True
  Chemical AI  │ H2 VQE -1.85727503 Ha  ·  exact -1.85727503 Ha  ·  |Δ| 4.4e-16
```

- **Quantum ML.** The quantum kernel scored at chance on this toy data, while a classical RBF kernel with an untuned γ did much better. This is reported as a negative result.
- **Chemical AI.** The H2 coefficients (0.735 Å, 2-qubit form) come from the Qiskit tutorials, Apache-2.0; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
- **Scope.** These are exact classical simulations of toy problems, not QPU runs, not evidence of quantum advantage, and not connected to the OES detector.

`src/mqoes/quantum_lab.py` (the original three demos behind `run.py quantum`) is kept as a separate module. Its two small edits (full-unitary equivalence and a `math.fsum` change for cross-version reproducibility) are listed in [CHANGES.md](CHANGES.md); its report is unchanged.

## Tests

```bash
python3 -m unittest discover -s tests -v     # 72 tests
```

The suite passes from the repository root and from any other directory. It covers:

- **Original suite:** replay validation and scoring boundaries; deterministic ranking and hashes; event and localisation metrics; threshold comparison; OES32 strict-equality and adaptive-tau behaviour; outage quorum/correlation logic; capsule validation; the quantum toy modules; and loopback-only binding.
- **Regression tests for the fixes:**
  - capsule overflow;
  - full-unitary circuit equivalence;
  - Host-header checks and request timeouts;
  - LF/CRLF line splitting;
  - generic 500 responses.
- **Stress experiment:** generator determinism, manifest hashes, calibration from the calibration split only, and byte-identical report regeneration.
- **Intersection module:** gate identities, parameter shift against finite differences, QAOA against brute force, VQE within 1e-6 of exact diagonalisation, determinism and isolation from the detector.

CI runs the suite on Python 3.11–3.13. It also checks that every committed report in `reports/` reproduces byte for byte.

## Provenance

This repository was rebuilt from files shared by Samsung Quick Share. Every file was matched to its original name by SHA-256.

- `run.py` was missing from the share and was added as a minimal shim that calls `mqoes.cli.main`.
- [RECONSTRUCTION_NOTES.md](RECONSTRUCTION_NOTES.md) records the reconstruction and the inferred layout.
- [CHANGES.md](CHANGES.md) records every subsequent edit.

## Limitations

- **Synthetic only.** A real evaluation needs:
  - authorised data;
  - documented units, scaling, channel order and preprocessing;
  - a separate calibration set and a held-out replay.
- **The OES32 stream model is float-level.** It does not reproduce fixed-point rounding or saturation, AXI stream timing, throughput or synthesis.
- **The stress results describe these generators only.** The calibration rule (pooled no-event FP rate) let spike artifacts set both thresholds. Other preregistered rules might behave differently.
- **The quantum demos are 2–5 qubits** with plain gradient descent from deterministic starts. QAOA reaches a stationary point that is not guaranteed to be global.
- **Chemical AI is limited to the H2 VQE toy.** It is not a chemistry workflow.

## Related work

- **[oes32-hls](https://github.com/sparkainlp-x/oes32-hls)** ([concept DOI 10.5281/zenodo.22985525](https://doi.org/10.5281/zenodo.22985525)): a C++ HLS research prototype with a `g++` testbench and Python bindings; FPGA synthesis is **UNRUN**. Its streaming kernel `oes32_triage_accelerator` (v2 algorithm) is the reference implementation of OES32 triage. The Python `stream32` model here is a float-level model of the **earlier v1** branch logic, so it is not a model of that kernel. It differs in four ways:
  - **Reset:** it applies reset once, before the first packet, and does not clamp it. The v2 kernel loads a clamped tau and its reset is level-sensitive.
  - **Sign:** it uses the signed value, while v2 uses |value|.
  - **Shock packets:** in this model they can raise tau; in v2 they cannot.
  - **Gain:** this model uses η = 0.05 and leak 0.95; v2 uses fixed-point 1/16 steps.

  See [RECONSTRUCTION_NOTES.md](RECONSTRUCTION_NOTES.md) and [CHANGES.md](CHANGES.md) (item 6).
- **[oes-resilience](https://github.com/sparkainlp-x/oes-resilience)** ([DOI 10.5281/zenodo.23071166](https://doi.org/10.5281/zenodo.23071166)): an open, reproducible benchmark for multichannel telemetry anomaly detection, with OES32 as its transparent reference detector.
- **[oes32-residual](https://github.com/sparkainlp-x/oes32-residual)** ([DOI 10.5281/zenodo.22985521](https://doi.org/10.5281/zenodo.22985521)): the normative OES-32 residual reference.

## Citation

See [CITATION.cff](CITATION.cff); GitHub shows a "Cite this repository" button. Releases are archived on Zenodo under the concept DOI [10.5281/zenodo.23113851](https://doi.org/10.5281/zenodo.23113851), which covers all versions. Each release also gets its own version DOI on Zenodo: v0.1.2 (metadata update) is [10.5281/zenodo.23241691](https://doi.org/10.5281/zenodo.23241691), v0.1.1 (enriched metadata and citation docs) is [10.5281/zenodo.23117526](https://doi.org/10.5281/zenodo.23117526) and v0.1.0 is [10.5281/zenodo.23113852](https://doi.org/10.5281/zenodo.23113852). Cite a version DOI when you need to refer to exact code.

### How to cite

APA:

> Brisson, J.-F. (2026). *Multi-Quantum OES: Offline OES replay triage workbench with an isolated AI ∩ quantum toy lab* [Computer software]. Zenodo. https://doi.org/10.5281/zenodo.23113851

BibTeX:

```bibtex
@software{brisson_multi_quantum_oes,
  author    = {Brisson, Jean-François},
  title     = {{Multi-Quantum OES: offline OES replay triage workbench with an isolated AI ∩ quantum toy lab}},
  year      = {2026},
  publisher = {Zenodo},
  doi       = {10.5281/zenodo.23113851},
  url       = {https://doi.org/10.5281/zenodo.23113851}
}
```

## License

This software is available under the GNU Affero General Public License v3.0 only (AGPL-3.0-only); see [LICENSE](LICENSE). Third-party material is listed in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

Organizations that want to use it in proprietary products or services without AGPL obligations can contact the author about a commercial license via https://sparkainlpx.xyz. See [COMMERCIAL-LICENSE.md](COMMERCIAL-LICENSE.md).
