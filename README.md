# WITNESS: Firmware-Assisted Checkpoint Recovery for AI Training

A software prototype of a proposed SSD firmware feature: an SSD tracks which AI-training
checkpoint "generations" it has durably sealed, and can report that state through an
out-of-band management path (simulated NVMe-MI, relayed by a simulated BMC) even when its
own host's OS/CPU has hung.

**The one claim this project makes:** when a training node's OS/CPU hangs but its SSD and BMC
stay powered (a real, common failure mode in large GPU clusters — it's exactly why remote
power-cycling via a BMC exists), a Commit Coordinator can query that SSD directly and recover
a checkpoint shard that a conventional collective-barrier scheme (modeled here after
PyTorch Distributed Checkpoint) would discard after waiting out a timeout.

**What this project does not claim:** it does not handle arbitrary host failures, and it does
not help at all if the whole server or rack loses power. It also does not claim the checkpoint
log page it proposes exists in any commercial SSD today — that payload is this project's
proposed extension on top of real, existing standards (NVMe FDP, NVMe-MI, UBM/SMBus, BMC).

See `docs/ARCHITECTURE.md` for the full real-vs-proposed-vs-simulated breakdown, protocol
invariants, and known limitations.

## Quick start

```
pip install -r requirements.txt
python -m pytest tests/ -v          # correctness invariants (10 tests)
python -m demo.run_all_and_report   # everything in one pass: all scenarios + metrics + scale sweep
```

## The main demo

```
python -m demo.run_scenario_b_host_hang
```

This is the one to show. Same injected fault (rank-0's host OS/CPU hangs before its
checkpoint write completes) run against both coordinators, walked through as nine explicit
steps with a live, event-by-event narration of what each coordinator actually observes:

```
1. checkpoint generation starts on N ranks
2. checkpoint data is written to each rank's local SSD (FDP-tagged)
3. [FAULT] rank-0's host OS/CPU hangs -- before its write completes
4. BASELINE loses in-band visibility into rank-0, cannot confirm it
5. WITNESS detects rank-0's host is unreachable in-band
6. WITNESS's coordinator falls back to the BMC/NVMe-MI out-of-band path
7. rank-0's SSD reports SEALED over that path
8. WITNESS commits the checkpoint
9. recovery: a restarting job asks for the latest valid generation -> O(1) answer
```

```
HOST_HANG   BASELINE   REJECTED    300.00 s   (rank's disk was actually SEALED)
HOST_HANG   WITNESS    COMMITTED     0.23 s   (recovered via simulated BMC/NVMe-MI path)
```

The baseline's number is exactly however long its configured barrier timeout is — that's
the failure mode: it has no other way to find out. WITNESS's number is bounded by one
out-of-band poll round trip. The baseline itself is a faithful model of PyTorch Distributed
Checkpoint's real collective-barrier design (same fault, same hardware, a realistic 300s
timeout) — not weakened to make WITNESS look better.

## All demos

| # | Script | What it shows |
|---|---|---|
| 1 | `python -m demo.run_normal` | No failures. Sanity check: both coordinators commit identically. |
| 2 | `python -m demo.run_scenario_b_host_hang` | **The main demo.** Host OS/CPU hang, SSD+BMC alive: baseline rejects, WITNESS recovers, live narrated timeline. |
| 3 | `python -m demo.run_ssd_failure` | No regression: if the drive itself dies, both coordinators correctly reject — no data to recover. |
| 4 | `python -m demo.run_bmc_partition_heals` | The management path itself goes unreachable, then heals inside the fencing window — WITNESS retries and still commits correctly. |
| 5 | `python -m demo.run_bmc_partition_permanent` | Same fault, never heals — WITNESS rejects, but only after the fencing window is genuinely exhausted, never on a bare first-try timeout. |
| 6 | `python -m demo.run_full_power_loss` | The scope boundary, shown on purpose: whole chassis loses power, WITNESS claims no advantage, behaves like the baseline. |
| 7 | `python -m demo.run_scale_sweep` | Commit/recovery latency as N (simulated ranks/SSDs) scales from 8 to 4096; writes `scale_sweep.{csv,png}`. |
| 8 | `python -m demo.run_all_and_report` | Runs everything above and prints one consolidated, judge-friendly comparison table + headline numbers. |

Every demo prints the same legend at the top, distinguishing real standards, this project's
proposed firmware feature, and pure simulation — so no claim in the output is ambiguous about
its status.

## Layout

- `witness/` — the simulation core:
  - `clock.py` — discrete-event virtual clock (N=4096 and a 300s timeout cost no real wall time)
  - `ftl.py` — SSD/FTL with FDP-style per-generation write tracking and sealing
  - `nvme_mi.py` — the proposed checkpoint-seal log page (real struct-packed wire format)
  - `bmc.py` — simulated out-of-band relay (UBM/SMBus + NVMe-MI properties, not real wire signaling)
  - `node.py` — one rank = host process + SSD + BMC, each independently failable
  - `coordinator.py` — `BaselineCoordinator` and `WitnessCoordinator`, plus an optional
    `on_event` narration hook (pure observability, changes no decision logic)
  - `metrics.py` — result objects and metadata-overhead accounting
  - `simulation.py` — orchestration (`build_nodes`, `run_scenario`)
- `tests/test_witness.py` — 10 tests encoding the protocol invariants (not just demo output)
- `demo/` — `scenarios.py` (shared scenario construction, used by every script below) plus the
  8 runnable demos listed above
- `docs/ARCHITECTURE.md` — frozen scope, real-vs-proposed-vs-simulated table, protocol
  invariants, known limitations
