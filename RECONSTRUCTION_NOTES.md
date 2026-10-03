# Reconstruction notes (added, not original)
Files restored from Quick Share hashes; names from the share page's per-file `hash`/`name` metadata.
`run.py` and this file were added during reconstruction. No README file was in the share.
Placement of report files under reports/ and examples/ is inferred from code paths.

## OES32 tau on reset: behavior not changed, documented only
`src/mqoes/stream32.py` (reset branch, about lines 43-47) sets `tau = tau_sensitivity` without
clamping. Clamping to [0.05, 4.0] happens only in the per-packet state updates. When
`reset_state` is false, tau starts at 1.0.

The Quick Share download did not include `oes32_triage.cpp`. A separate archive found later,
`oes32-triage-hls-v0.1.0.zip` (`src/oes32_triage.cpp`/`.hpp`; not part of this repository), was
read but not imported. It shows:
- `static data_t adaptive_tau = 1.0;` and `if (reset_state) adaptive_tau = tau_sensitivity;`
  have no clamp. The Python behavior matches that.
- `data_t` is `ap_fixed<24, 8, AP_RND, AP_SAT>`, so in hardware the value saturates at the
  fixed-point range (about [-128, 128)), not at [0.05, 4.0]. The Python float model does not
  reproduce this rounding or saturation, as the README already says.
- The HLS function handles **one packet per call** and checks `reset_state` on **every call**.
  If `reset_state` stays asserted across packets, hardware resets tau before every packet.
  The Python model applies `reset_state` only once, before the first packet of the list. The
  bundled example (`reset_state: true`, 4 packets) therefore models "reset, then run 4 packets
  with reset deasserted". It does not model reset held high.

Nothing was changed. Whether the reset is meant as a one-shot pulse is an open question for
the author, and it is not decidable from the code alone. Confirm whether that archive is the
authoritative source.

## Post-reconstruction fixes
See CHANGES.md. The pristine reconstructed tree is not included in this repository; its files are
the Quick Share originals listed above, and CHANGES.md describes every edit made to them.
