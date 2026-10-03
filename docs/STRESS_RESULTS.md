# Stress replay: OES-512 block score vs per-block max-abs baseline

**Verdict: negative / no demonstrated advantage.** On this held-out synthetic stress replay the
block score did **not** beat the max-abs baseline in event detection under either preregistered
protocol:
- At the shared 0.50 threshold it detected fewer events (5/11 vs 8/11) and had fewer FP frames
  (45/272 vs 81/272), but more FP alarm episodes (26 vs 14).
- With thresholds calibrated on a separate split to the same 5% no-event FP rate, the two
  detectors produced **identical** results: 0/11 events detected and 17/272 FP frames each.

The data are invented. There are only 11 events, so none of these differences is statistically
meaningful. This tests behavior on toy data; it does not validate either detector.

## What was run (in this order; file mtimes 2026-10-02, ET)
1. 22:06:00: `tools/generate_stress.py` wrote `data/stress_replay.jsonl` (held-out test split,
   seed 20261003) and `data/stress_calibration.jsonl` (calibration split, seed 20261004). Each
   has 320 frames, 6 regimes and 11 labeled events.
2. 22:06:09: `prereg/stress_locked.json` was locked with the untuned example thresholds
   (candidate 0.50, baseline 0.50).
3. 22:06:09: `tools/calibrate_stress.py` read **only the calibration split** and wrote
   `prereg/stress_calibrated_locked.json` plus `reports/stress-calibration.json`.
4. 22:06:14: `prereg/STRESS_LOCK_MANIFEST.json` recorded the sha256 of the data, preregs, tools
   and calibration record.
5. 22:06:14: `run.py evaluate` on the held-out replay wrote `reports/stress-report-fixed-0.50.json`
   and `reports/stress-report-calibrated.json`.

No statistics of the stress set were looked at before step 5, and no threshold or parameter was
changed afterwards.

**Disclosure:** the self-declared `locked_at`/`recorded_at` strings (22:06:30, 22:07:00,
22:07:30) were typed by hand and run 20-80 s *ahead* of the real file times above. The order is
correct, and the hashes are the evidence. As the README notes, `locked_at` cannot prove when a
protocol was frozen.

**Design caveat (fairness):** I designed the generator knowing the score formula. I tried to
balance it with regimes expected to favor each detector:
- expected to favor the block score: weak multi-channel bursts, sustained drift, spike artifacts;
- expected to favor the baseline: sparse 3-channel spike events.

All generative parameters are listed in `tools/generate_stress.py` (`PARAMETERS`), summarized here:

| regime | frames | events | generative model (base noise N(0, 0.03) unless stated) |
|---|---|---|---|
| stress_stable_control | 40 | 0 | background only |
| stress_heavy_tail_noise | 60 | 0 | Student-t(df=2)*0.03 on all channels; p=0.30/frame one isolated spike of size 0.60-1.20 |
| stress_spike_artifacts | 40 | 0 | p=0.80/frame one single-channel artifact of size 0.60-1.50, labeled `none` |
| stress_weak_multichannel_burst | 60 | 4 x 3 frames | 1-2 blocks; all 32 channels offset by sign*U(0.30, 0.46) |
| stress_sustained_drift | 60 | 3 x 8 frames | 1-3 blocks; all 32 channels ramp to sign*U(0.35, 0.60) over 8 frames, jitter N(0, 0.02) |
| stress_sparse_spike_event | 60 | 4 x 3 frames | 1 block; 3 channels offset by sign*U(0.70, 1.00) |

## Calibration rule (prereg `stress_calibrated_locked.json`)
- Use only the no-event frames of the calibration split (272 frames), pooled across all regimes.
- Per detector, a frame's statistic is the maximum over its 16 blocks of that detector's block
  value.
- Target FP rate alpha = 0.05, so k = floor(0.05 * 272) = 13 frames may alarm.
- Threshold = the (k+1)-th largest statistic, rounded up to 4 decimals and strictly above it.

Result: candidate threshold **0.7436** (14th-largest statistic 0.7435912553214994); baseline
threshold **1.4265** (14th-largest 1.42640338). Each threshold gives 13 FP frames on the
calibration split. On the held-out split the realised pooled FP rate was 17/272 = 6.25%.

## Per-regime results (numbers copied verbatim from the reports)
"FP" counts are on no-event frames. Block TP/FP/FN are micro-totals across annotated frames,
including no-event frames, where every flagged block counts as FP.

### reports/stress-report-fixed-0.50.json
protocol_id=oes512-stress-fixed-0.50-v1 sha256=40d52a32fe450336d9cacc8d317eabd81451fa8cd3831d68228204c3d7af321b candidate_threshold=0.5 baseline_threshold=0.5 input_sha256=2d7b71f1283ac32fc21ccf5b2ecdce139d9f725854dd8d8bdb1ee6e7325682eb

| regime | detector | events detected/total | event_recall | missed | FP frames / no-event frames | FP rate | FP episodes | block TP/FP/FN | precision | recall | IoU |
|---|---|---|---|---|---|---|---|---|---|---|---|
| stress_heavy_tail_noise | candidate | 0/0 | null | 0 | 25/60 | 0.4166666666666667 | 16 | 0/31/0 | 0.0 | null | 0.0 |
| stress_heavy_tail_noise | baseline | 0/0 | null | 0 | 53/60 | 0.8833333333333333 | 7 | 0/118/0 | 0.0 | null | 0.0 |
| stress_sparse_spike_event | candidate | 3/4 | 0.75 | 1 | 0/48 | 0.0 | 0 | 9/0/3 | 1.0 | 0.75 | 0.75 |
| stress_sparse_spike_event | baseline | 4/4 | 1.0 | 0 | 0/48 | 0.0 | 0 | 12/0/0 | 1.0 | 1.0 | 1.0 |
| stress_spike_artifacts | candidate | 0/0 | null | 0 | 20/40 | 0.5 | 10 | 0/20/0 | 0.0 | null | 0.0 |
| stress_spike_artifacts | baseline | 0/0 | null | 0 | 28/40 | 0.7 | 7 | 0/28/0 | 0.0 | null | 0.0 |
| stress_stable_control | candidate | 0/0 | null | 0 | 0/40 | 0.0 | 0 | 0/0/0 | null | null | null |
| stress_stable_control | baseline | 0/0 | null | 0 | 0/40 | 0.0 | 0 | 0/0/0 | null | null | null |
| stress_sustained_drift | candidate | 2/3 | 0.6666666666666666 | 1 | 0/36 | 0.0 | 0 | 8/0/32 | 1.0 | 0.2 | 0.2 |
| stress_sustained_drift | baseline | 3/3 | 1.0 | 0 | 0/36 | 0.0 | 0 | 10/0/30 | 1.0 | 0.25 | 0.25 |
| stress_weak_multichannel_burst | candidate | 0/4 | 0.0 | 4 | 0/48 | 0.0 | 0 | 0/0/18 | null | 0.0 | 0.0 |
| stress_weak_multichannel_burst | baseline | 1/4 | 0.25 | 3 | 0/48 | 0.0 | 0 | 1/0/17 | 1.0 | 0.05555555555555555 | 0.05555555555555555 |

### reports/stress-report-calibrated.json
protocol_id=oes512-stress-calibrated-fpr0.05-v1 sha256=cc663d92773337ac27fa2e78be2d40fb07cf66b9b7ecedef0f0c83d097069c92 candidate_threshold=0.7436 baseline_threshold=1.4265 input_sha256=2d7b71f1283ac32fc21ccf5b2ecdce139d9f725854dd8d8bdb1ee6e7325682eb

| regime | detector | events detected/total | event_recall | missed | FP frames / no-event frames | FP rate | FP episodes | block TP/FP/FN | precision | recall | IoU |
|---|---|---|---|---|---|---|---|---|---|---|---|
| stress_heavy_tail_noise | candidate | 0/0 | null | 0 | 14/60 | 0.23333333333333334 | 12 | 0/14/0 | 0.0 | null | 0.0 |
| stress_heavy_tail_noise | baseline | 0/0 | null | 0 | 14/60 | 0.23333333333333334 | 12 | 0/14/0 | 0.0 | null | 0.0 |
| stress_sparse_spike_event | candidate | 0/4 | 0.0 | 4 | 0/48 | 0.0 | 0 | 0/0/12 | null | 0.0 | 0.0 |
| stress_sparse_spike_event | baseline | 0/4 | 0.0 | 4 | 0/48 | 0.0 | 0 | 0/0/12 | null | 0.0 | 0.0 |
| stress_spike_artifacts | candidate | 0/0 | null | 0 | 3/40 | 0.075 | 2 | 0/3/0 | 0.0 | null | 0.0 |
| stress_spike_artifacts | baseline | 0/0 | null | 0 | 3/40 | 0.075 | 2 | 0/3/0 | 0.0 | null | 0.0 |
| stress_stable_control | candidate | 0/0 | null | 0 | 0/40 | 0.0 | 0 | 0/0/0 | null | null | null |
| stress_stable_control | baseline | 0/0 | null | 0 | 0/40 | 0.0 | 0 | 0/0/0 | null | null | null |
| stress_sustained_drift | candidate | 0/3 | 0.0 | 3 | 0/36 | 0.0 | 0 | 0/0/40 | null | 0.0 | 0.0 |
| stress_sustained_drift | baseline | 0/3 | 0.0 | 3 | 0/36 | 0.0 | 0 | 0/0/40 | null | 0.0 | 0.0 |
| stress_weak_multichannel_burst | candidate | 0/4 | 0.0 | 4 | 0/48 | 0.0 | 0 | 0/0/18 | null | 0.0 | 0.0 |
| stress_weak_multichannel_burst | baseline | 0/4 | 0.0 | 4 | 0/48 | 0.0 | 0 | 0/0/18 | null | 0.0 | 0.0 |

### Totals across regimes (summed from the report fields)
| protocol | detector | events detected | FP frames | FP episodes | block TP/FP/FN |
|---|---|---|---|---|---|
| fixed 0.50 | candidate | 5/11 | 45/272 | 26 | 17/51/53 |
| fixed 0.50 | baseline | 8/11 | 81/272 | 14 | 23/146/47 |
| calibrated | candidate | 0/11 | 17/272 | 14 | 0/17/70 |
| calibrated | baseline | 0/11 | 17/272 | 14 | 0/17/70 |

## Interpretation
- **Equal thresholds cannot favor the block score on recall.** Since RMS <= peak and mean <= peak,
  score = 0.45*peak + 0.35*RMS + 0.20*mean <= peak. At a shared threshold the candidate can
  only flag a subset of the baseline's blocks (a test checks this bound on the stress data).
  So at 0.50 it is simply the more conservative detector: fewer FP frames, lower recall. Its
  FP alarms come in more but shorter runs (26 vs 14 episodes).
- **Weak multi-channel bursts (offsets 0.30-0.46):** the block score never reached 0.50
  (0/4); the baseline caught 1/4 from noise pushing one channel over 0.50. The idea that RMS and
  mean terms help here only holds when the candidate threshold is set *below* the burst
  amplitude, which neither preregistered protocol did.
- **Calibrated to equal FP rate,** both thresholds ended up driven by the largest no-event
  outliers: isolated spikes and heavy tails. Because a single dominant spike raises both
  statistics in step (score is about 0.52 x spike), both detectors flagged exactly the same
  no-event frames and missed every event. Pooling artifact-heavy regimes into a 5% FP target
  makes both detectors blind to events at 0.3-1.0 amplitude. That is a property of this
  calibration rule and data mix, not evidence for either score.
- **Not done, to avoid post-hoc tuning:** calibrating per regime, on stable controls only, or with
  a different alpha. Any of these would need a new preregistration and a fresh held-out split.
