# WITNESS: Firmware-Assisted Checkpoint Recovery for AI Training

## Scope, frozen

**Novel contribution:** a proposed SSD firmware feature that tracks checkpoint-generation
state and exposes a durable "sealed" status through NVMe-MI. In the specific failure case
where the host OS/CPU hangs but the SSD and BMC remain powered (Scenario B), an external
Commit Coordinator queries the SSD through the BMC/UBM management path and recovers a valid
checkpoint shard without relying on the failed host.

**Explicitly not claimed:**
- Arbitrary host failures. Only OS/CPU hangs with the SSD and BMC still powered.
- Complete server/rack power loss (Scenario C). The OOB path goes dark with everything else;
  recovery there is identical to a plain durable-write ledger.
- That the checkpoint-seal log page exists in any commercial SSD today. It doesn't. It's this
  project's proposed firmware extension.

This scope has not changed since it was frozen; everything below documents the finished
implementation of exactly that scope.

## What's real vs. what's simulated

| Component | Status |
|---|---|
| NVMe Flexible Data Placement (per-checkpoint write tagging) | Real, ratified standard (NVMe 2.0+) |
| NVMe-MI (out-of-band Admin-command tunneling, incl. Get Log Page) | **Real, ratified standard** — the TRANSPORT only. Confirmed present on shipping SanDisk/WD Ultrastar drives (NVMe-MI 1.1/1.1b). |
| UBM / SMBus sideband from drive bay to BMC | Real, standard backplane management practice (OCP-class servers) |
| BMC as an independent, separately-powered service processor | Real; this is exactly what lets ops teams power-cycle a hung GPU node today |
| **Checkpoint-generation "sealed" log page (the PAYLOAD carried over NVMe-MI)** | **Proposed by this project, not a standard.** No commercial firmware defines this payload — only the NVMe-MI transport that would carry it is real. `witness/nvme_mi.py` is our concrete proposal for its wire format. Never describe this payload as an existing NVMe feature. |
| SSD/FTL behavior, NAND write bandwidth model | Simulated (`witness/ftl.py`) — a simplified model, not a NAND simulator |
| BMC relay latency/reachability | Simulated (`witness/bmc.py`) — models the *properties* that matter (host-independent reachability, slower than in-band) without real SMBus wire signaling |
| Commit Coordinator, fencing, OOB fanout limits | Real code, run in this repo (`witness/coordinator.py`) — genuine coordination logic, not a mock |

Every demo script prints this same distinction at startup (`demo._common.print_legend()`), so
no claim made in the console output is ambiguous about its status.

## The three scenarios, and what this system claims for each

| Scenario | SSD | BMC | Host process | WITNESS advantage |
|---|---|---|---|---|
| A: process dies, machine fine | up | up | dead | None claimed — a local in-band sidecar already solves this; not the interesting case |
| **B: OS/CPU hangs** | **up** | **up** | **dead** | **This is the design's entire claim.** OOB path bypasses the dead host. |
| C: full power loss | down | down | dead | None. Degrades to the same behavior as a plain durable-write ledger. |

## Architecture

```
[Rank i: host process] --writes shard, tagged Gen=G (FDP-style)--> [Sim SSD/FTL]
        |  (in-band self-report, fast path -- covers Scenario A)         |
        |                                                     on full shard written:
        |                                                     seal(G) = durable, local
        v                                                                |
  [BaselineCoordinator]                                        [Sim BMC] <--- OOB query,
  waits for in-band acks                                     independent of host process
  from ALL ranks, or times                                    liveness (Scenario B only)
  out and rejects the whole                                              |
  generation                                                             v
                                                                [WitnessCoordinator]
                                                       in-band first; falls back to OOB
                                                       when process is dead; UNKNOWN (BMC
                                                       unreachable) triggers fencing/retry,
                                                       never an immediate reject
                                                                           |
                                                                           v
                                                                  [CommitLog: O(1) lookup]
                                                          recovery = "highest committed G"
```

## Protocol invariants enforced in code (not just described)

1. **Never conflate "not sealed yet" with "failed."** `WitnessCoordinator` treats a reachable
   device's `NOT_SEALED` answer as `PENDING`, not `REJECTED` — only an exhausted fencing window
   (persistent unreachability) or the outer `round_timeout_ms` backstop can produce a reject.
   (An earlier draft of this coordinator got this wrong — the test that caught it is
   `tests/test_witness.py::test_witness_recovers_the_same_case_baseline_rejects`, and the fix
   is documented in its commit history / this file's changelog below.)
2. **Never treat "unreachable" as "sealed=False."** An unreachable BMC returns `None`
   (`UNKNOWN`), which is retried within `fencing_timeout_ms`, not immediately rejected. Demos 4
   and 5 exist specifically to show both halves of this: heals-in-time still commits,
   never-heals still eventually (correctly) rejects.
3. **The commit log is the only thing recovery reads.** `CommitLog.latest_committed()` is an
   O(1) lookup — no re-read of checkpoint data.
4. **A dead SSD gets no special treatment from either path.** `get_seal_status()` returning
   `None` for an unpowered device is read identically by the in-band and OOB paths — neither
   can fabricate an answer the device itself can't give (demo 3).

## Baseline fidelity

`BaselineCoordinator` is a faithful model of PyTorch Distributed Checkpoint's real
collective-barrier design, not a strawman weakened to make WITNESS look better: it writes the
same shards to the same simulated hardware, waits for genuine in-band acknowledgment from every
rank, and uses a realistic default timeout (300s, in line with production `torchrun`-style
configuration). It fails in the demos for exactly one specific, real reason — once a rank's
host process is unreachable, the barrier has no other channel to ask anything, and must wait
out its full timeout before it can even guess. That is the actual, structural limitation this
project targets, not an artifact of how the baseline was implemented here.

## Assumptions this design relies on

- The host process, the SSD controller, and the BMC are on genuinely separate failure domains
  in real hardware — this is standard for OCP-class/enterprise servers with hot-swap NVMe bays
  wired through a UBM backplane, not a given for every server form factor (e.g. a soldered M.2
  boot drive with no BMC-wired sideband would not have this property).
- A BMC/management-network path, once established, is slower than the in-band NVMe queue path
  (modeled as `bmc_query_latency_ms`, default 15 ms) — SMBus/I2C is a low-bandwidth management
  bus, not a data path, and the design does not depend on it being fast, only reachable.
- Write durability acknowledgment from the SSD is honest: once `get_seal_status()` returns
  `SEALED`, that data is assumed genuinely non-volatile. This is inherited from existing SSD
  power-loss-protection hardware, not something this project adds.

## Known limitations (stated, not hidden)

- The Commit Coordinator's own commit log is a single in-memory object here. Production would
  replicate it across a small quorum of devices or an external store; that replication is not
  built in this prototype.
- The FTL model uses a simple bandwidth-delay write model, not a real NAND/GC/wear-leveling
  simulator (WARP/FEMU-style) — sufficient for this claim, not a general SSD simulator.
- `max_concurrent_oob_queries` models a real constraint (BMC/management-network fanout limits)
  but with an illustrative default value in the scale-sweep demo, not a number taken from a
  real BMC's measured capacity.
- The `on_event` narration hook in both coordinators exists purely to make demo output legible
  (it reports only what each coordinator already observes); it is not part of the protocol and
  has no effect on any commit/reject decision — every test passes identically with or without
  it attached.
- No real SMBus/I2C, NVMe-MI wire protocol, or BMC firmware is touched anywhere in this
  repository. All timing and reachability properties of the OOB path are modeled, not measured.

## Demo instructions

```
pip install -r requirements.txt
python -m pytest tests/ -v
```

Then run demos 1–8 individually (see README.md for the full table), or run everything at once:

```
python -m demo.run_all_and_report
```

The main demo to show live is `python -m demo.run_scenario_b_host_hang` — it walks through the
fixed nine-step story (checkpoint starts -> data written -> host OS/CPU fails -> baseline loses
in-band visibility -> WITNESS detects the failure -> falls back to the BMC/NVMe-MI OOB path ->
SSD reports SEALED -> WITNESS commits -> recovery selects the valid generation), narrating each
coordinator's actual events live, and ends in a side-by-side verdict/latency comparison plus the
ground-truth disk state proving the baseline's rejection was avoidable. It does not touch SSD
failure or full power loss — those are demos 3 and 6, kept separate so this demo cannot be read
as claiming more than Scenario B.
