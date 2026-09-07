"""
Demo 2/8 -- THE MAIN DEMO. Scenario B: host OS/CPU hangs, SSD + BMC stay
powered. This is the one failure case WITNESS is built for; everything
else in this repo exists to scope that claim honestly, not to compete
with it.

Fixed story, walked through as explicit numbered steps:

  1. Checkpoint generation starts on N ranks.
  2. Checkpoint data is written to each rank's local SSD (FDP-tagged).
  3. [FAULT] rank-0's host OS/CPU hangs -- BEFORE its write completes.
     (simulated: e.g. a kernel panic, an ECC-triggered machine-check
     exception, or an NVIDIA Xid error wedging a GPU node -- all real,
     common causes of exactly this failure mode in production clusters)
  4. BASELINE loses in-band visibility into rank-0 and cannot confirm it.
  5. WITNESS detects that rank-0's host is unreachable in-band.
  6. WITNESS's coordinator falls back to the BMC/NVMe-MI OOB path.
  7. Rank-0's SSD reports SEALED over that path.
  8. WITNESS commits the checkpoint.
  9. Recovery: a restarting job asks "what's the latest valid generation?"
     and gets an O(1) answer.

The baseline here is a faithful model of real production distributed
checkpointing (PyTorch Distributed Checkpoint's collective-barrier
design) -- it is not artificially handicapped. It fails for exactly one
specific, real reason: once rank-0's host process is unreachable, the
barrier has no other channel to ask anything, and must wait out its
full configured timeout before it can even guess.
"""

import demo._common as common
import demo.scenarios as scenarios
from witness.ftl import SealStatus


def narrator(prefix):
    def _emit(t_ms, msg):
        print(f"  [{prefix:8s} t={t_ms:9.1f}ms] {msg}")
    return _emit


def step(n: int, text: str):
    print(f"\n--- STEP {n}: {text} ---")


def main():
    common.print_banner("DEMO 2/8 -- MAIN DEMO -- SCENARIO B: host OS/CPU hang, SSD + BMC alive")
    common.print_legend()

    print("Setup: 8 simulated ranks, checkpoint generation 1, 10 MB shard each.")
    print("Fault: rank-0's host OS/CPU hangs (simulated) well BEFORE its write completes.")
    print("       Its SSD keeps writing regardless -- the write timer is independent of")
    print("       the host process -- and its BMC stays powered and reachable.\n")
    print("Fairness note: the baseline below is a faithful model of PyTorch Distributed")
    print("Checkpoint's real collective-barrier design, not a strawman. It is given the")
    print("same fault, the same hardware, and a realistic 300s timeout. It fails for one")
    print("specific, real reason: rank-0's host is its only channel, and that channel is gone.")

    step(1, "Checkpoint generation 1 starts on 8 ranks")
    step(2, "Checkpoint data is written to each rank's local SSD (FDP-tagged)")
    step(3, "[FAULT] rank-0's host OS/CPU hangs -- before its write completes")

    step(4, "BASELINE: in-band collective barrier -- can it confirm rank-0?")
    print(">>> Running BASELINE (PyTorch-DCP-style collective barrier) <<<\n")
    result_b, commit_log_b, nodes_b = scenarios.run_host_hang("baseline", on_event=narrator("BASELINE"))
    disk_truth_b = nodes_b[0].ssd.get_seal_status(1)
    print(f"\nBASELINE cannot confirm rank-0: its only channel (the host process) is dead.")
    print(f"It must wait out its full configured barrier timeout before it can even guess.")

    step(5, "WITNESS: detects rank-0's host is unreachable in-band")
    step(6, "WITNESS: coordinator falls back to the BMC/NVMe-MI out-of-band path")
    step(7, "WITNESS: rank-0's SSD reports SEALED over that path")
    step(8, "WITNESS: commits the checkpoint once every rank is confirmed")
    print("\n>>> Running WITNESS (in-band first, BMC/NVMe-MI out-of-band fallback) <<<\n")
    result_w, commit_log_w, nodes_w = scenarios.run_host_hang("witness", on_event=narrator("WITNESS "))
    disk_truth_w = nodes_w[0].ssd.get_seal_status(1)

    step(9, "RECOVERY: a restarting job asks the coordinator for the latest valid generation")
    recovered_generation = commit_log_w.latest_committed()
    print(f"  query: CommitLog.latest_committed()  ->  answer: generation {recovered_generation}")
    print(f"  (O(1) lookup -- no re-read of checkpoint data required)")

    common.print_banner("RESULT")
    print(f"Ground truth on rank-0's disk in BOTH runs: {disk_truth_b} (baseline run) / "
          f"{disk_truth_w} (witness run)")
    print("  -- the data was safe all along in both runs. The only difference is whether")
    print("     the coordinator had any way to find that out.\n")

    print("(All latencies below are discrete-event SIMULATION TIME from this prototype, not")
    print(" measured hardware performance -- no real NAND, SMBus, or NVMe-MI wire protocol")
    print(" is touched anywhere in this repository.)\n")

    print(f"{'':10s} {'verdict':10s} {'latency (simulated)':>20s}   {'committed generation'}")
    print(f"{'BASELINE':10s} {result_b.verdict:10s} {common.fmt_ms(result_b.latency_ms):>20s}   "
          f"{commit_log_b.latest_committed()}")
    print(f"{'WITNESS':10s} {result_w.verdict:10s} {common.fmt_ms(result_w.latency_ms):>20s}   "
          f"{commit_log_w.latest_committed()}")

    if result_b.committed is False and result_w.committed is True and disk_truth_w == SealStatus.SEALED:
        speedup = result_b.latency_ms / result_w.latency_ms
        print(f"\nWITNESS recovered checkpoint data the baseline discarded, and reached its verdict "
              f"~{speedup:,.0f}x faster (simulated: {common.fmt_ms(result_b.latency_ms)} -> "
              f"{common.fmt_ms(result_w.latency_ms)}).")
        print("This speedup is specific to Scenario B (host OS/CPU hang) and is simulation time, not")
        print("a measured real-SSD/real-hardware benchmark. The baseline's number is exactly its")
        print("configured barrier timeout -- by construction, that's the failure mode: it had no")
        print("other way to find out. WITNESS's number is bounded by one OOB poll round trip.")

    print("\nScope reminder: this result is specific to Scenario B (host OS/CPU hang, SSD+BMC")
    print("alive). It says nothing about SSD failure or full power loss -- see demos 3 and 6.")


if __name__ == "__main__":
    main()
