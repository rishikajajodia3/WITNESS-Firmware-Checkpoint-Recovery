"""
Demo 8/8 -- runs every scenario and prints one consolidated, judge-friendly
report, organized into fixed sections: baseline result, WITNESS result,
recovery result, latency comparison, metadata overhead, failure scenarios,
scaling results, and limitations. Then triggers the scale sweep.

All latency numbers printed here are discrete-event SIMULATION TIME, not
measured hardware performance -- this is stated once up front and then
reinforced at every table, not left implicit.
"""

import demo._common as common
import demo.run_scale_sweep as sweep
import demo.scenarios as scenarios
from witness.coordinator import CommitLog
from witness.metrics import baseline_metadata_bytes, witness_metadata_bytes


def row(scenario, coordinator_kind, result, note=""):
    print(f"{scenario:26s} {coordinator_kind.upper():8s} {result.verdict:9s} "
          f"{common.fmt_ms(result.latency_ms):>10s}   {note}")


def section(title):
    print()
    print(f"--- {title} " + "-" * max(0, 74 - len(title)))


def main():
    n = common.DEFAULT_NUM_NODES
    common.print_banner(f"DEMO 8/8 -- FULL REPORT (N={n} simulated ranks/SSDs)")
    common.print_legend()
    print("NOTE: every latency figure in this report is discrete-event SIMULATION TIME from")
    print("this prototype, not measured hardware performance. No real NAND, SMBus, or NVMe-MI")
    print("wire protocol is touched anywhere in this repository.")

    # Run the headline scenario once, shared by the next three sections.
    result_b, commit_log_b, nodes_b = scenarios.run_host_hang("baseline", num_nodes=n)
    result_w, commit_log_w, nodes_w = scenarios.run_host_hang("witness", num_nodes=n)
    disk_truth_b = nodes_b[0].ssd.get_seal_status(1)
    disk_truth_w = nodes_w[0].ssd.get_seal_status(1)

    section("BASELINE RESULT (Scenario B: host OS/CPU hang, same hardware, same fault)")
    print(f"  verdict:  {result_b.verdict}")
    print(f"  latency:  {common.fmt_ms(result_b.latency_ms)} (simulated) -- exactly its configured "
          f"barrier timeout")
    print(f"  rank-0's actual disk state at that moment: {disk_truth_b}")
    print("  reason: rank-0's host process is its only channel; once that's unreachable, the")
    print("  in-band collective barrier has nothing else to ask and must wait out the timeout.")

    section("WITNESS RESULT (identical hardware, identical fault)")
    print(f"  verdict:  {result_w.verdict}")
    print(f"  latency:  {common.fmt_ms(result_w.latency_ms)} (simulated)")
    print(f"  rank-0's actual disk state at that moment: {disk_truth_w}")
    print("  reason: rank-0's SSD is queried directly via the simulated BMC/NVMe-MI")
    print("  out-of-band path, bypassing the dead host process entirely.")

    section("RECOVERY RESULT")
    recovered = commit_log_w.latest_committed()
    print(f"  query:  CommitLog.latest_committed()")
    print(f"  answer: generation {recovered}  (O(1) lookup, no re-read of checkpoint data)")

    section("LATENCY COMPARISON (all figures simulated ms, not hardware-measured)")
    for kind in ("baseline", "witness"):
        result, _, _ = scenarios.run_normal(kind, num_nodes=n)
        row("NORMAL (no failures)", kind, result)
    row("HOST_HANG (Scenario B)", "baseline", result_b, note=f"disk truth: {disk_truth_b}")
    row("HOST_HANG (Scenario B)", "witness", result_w, note=f"disk truth: {disk_truth_w}")
    for kind in ("baseline", "witness"):
        result, _, _ = scenarios.run_ssd_failure(kind, num_nodes=n)
        row("SSD_FAILURE", kind, result, note="both must reject -- no data to recover")
    print("    (WITNESS's shorter number here is faster/bounded REJECTION via active BMC")
    print("    re-probing, not a recovery advantage -- neither side recovers any data)")
    result_bh, _, _ = scenarios.run_bmc_partition_heals(num_nodes=n)
    row("BMC_PARTITION_HEALS", "witness", result_bh, note="commits after fencing retry")
    result_bp, _, _ = scenarios.run_bmc_partition_permanent(num_nodes=n)
    row("BMC_PARTITION_PERMANENT", "witness", result_bp, note="rejects after fencing exhausted")
    fpl_results = {}
    for kind in ("baseline", "witness"):
        result, _, _ = scenarios.run_full_power_loss(kind, num_nodes=n)
        fpl_results[kind] = result
        row("FULL_POWER_LOSS (out of scope)", kind, result,
            note=f"same {common.fmt_ms(scenarios.FULL_POWER_LOSS_TIMEOUT_MS)} timeout -- no OOB advantage")
    fpl_gap_ms = abs(fpl_results["witness"].latency_ms - fpl_results["baseline"].latency_ms)
    print(f"    (both coordinators use the SAME configured timeout here on purpose -- WITNESS has NO")
    print(f"    information advantage BECAUSE the SSD and BMC are also powered off, not just the")
    print(f"    host; the remaining ~{common.fmt_ms(fpl_gap_ms)} gap is per-poll/round-trip bookkeeping")
    print(f"    overhead, not a capability difference)")
    speedup = result_b.latency_ms / result_w.latency_ms
    print(f"\n  Headline: WITNESS reached its Scenario B verdict ~{speedup:,.0f}x faster than the "
          f"baseline\n  (simulated), while recovering data the baseline discarded unnecessarily.")

    section("METADATA OVERHEAD (illustrative sizes, not measured on real firmware)")
    log = CommitLog()
    log.commit(1)
    print(f"  baseline manifest (~{n} shards):                {baseline_metadata_bytes(n)} bytes")
    print(f"  WITNESS log pages + commit record ({n} shards):   {witness_metadata_bytes(n, log)} bytes")

    section("FAILURE SCENARIOS SUMMARY")
    print("  SSD_FAILURE            -- both coordinators correctly REJECT; no data existed to recover")
    print("  BMC_PARTITION_HEALS    -- WITNESS COMMITS once the OOB path recovers inside the")
    print("                           fencing window (never treats a bare timeout as proof of failure)")
    print("  BMC_PARTITION_PERMANENT-- WITNESS REJECTS, but only after the fencing window is")
    print("                           genuinely exhausted, not on the first failed query")
    print("  FULL_POWER_LOSS         -- both coordinators REJECT using the SAME configured timeout;")
    print("                           WITNESS claims NO advantage here, by design (host, SSD, and")
    print("                           BMC all lose power together -- the OOB path is dark too)")

    section("SCALING RESULTS")
    rows = sweep.sweep()
    sweep.save_csv(rows)
    sweep.save_plot(rows)
    sweep.summarize(rows)

    section("LIMITATIONS (see docs/ARCHITECTURE.md for full detail)")
    print("  - Every number above is a software simulation; no real SMBus/I2C, NVMe-MI wire")
    print("    protocol, or BMC firmware is touched anywhere in this repository.")
    print("  - The checkpoint-generation \"sealed\" log page is WITNESS's proposed firmware")
    print("    payload, not an existing NVMe feature -- only the NVMe-MI transport is real.")
    print("  - The Commit Coordinator's own commit log is a single in-memory object here;")
    print("    production would replicate it across a small quorum, which is not built here.")
    print("  - The FTL model is a simple bandwidth-delay write model, not a NAND/GC/wear-")
    print("    leveling simulator.")
    print("  - WITNESS claims an advantage ONLY for Scenario B (host OS/CPU hang, SSD+BMC")
    print("    alive). It claims nothing for SSD failure or full power loss, shown above.")


if __name__ == "__main__":
    main()
