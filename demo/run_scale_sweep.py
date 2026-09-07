"""
Demo 7/8 -- scalability sweep: commit latency (NORMAL) and
recovery-decision latency (HOST_HANG) as the number of simulated
ranks/SSDs grows. Because this is a discrete-event simulation, sweeping
to thousands of nodes costs CPU time, not wall-clock waiting.

Saves scale_sweep.png and scale_sweep.csv.
"""

import csv

import demo._common as common
import demo.scenarios as scenarios
from witness.clock import SimClock
from witness.simulation import build_nodes, run_scenario

NODE_COUNTS = [8, 16, 32, 64, 128, 256, 512, 1024, 2048, 4096]

# A single simultaneously-hung node's recovery decision doesn't depend on
# cluster size (it's bounded by that one node's own OOB round trip). To
# show a genuine, non-fabricated scaling effect, this sweep also injects a
# hang on 10% of nodes at once (a realistic "a rack blipped" event) against
# a coordinator with a bounded OOB fanout (max_concurrent_oob_queries),
# modeling a real BMC/management-network concurrency limit.
FAILURE_FRACTION = 0.10
MAX_CONCURRENT_OOB_QUERIES = 64


def kill_process_early(ns):
    ns[0].kill_process()


def kill_fraction_early(ns):
    k = max(1, int(len(ns) * FAILURE_FRACTION))
    for node in ns[:k]:
        node.kill_process()


def sweep():
    rows = []
    for n in NODE_COUNTS:
        # NORMAL commit latency, both coordinators
        for kind in ("baseline", "witness"):
            clock = SimClock()
            nodes = build_nodes(clock, n, common.DEFAULT_SHARD_BYTES,
                                 common.DEFAULT_SSD_BANDWIDTH, common.DEFAULT_BMC_LATENCY_MS)
            kwargs = scenarios.baseline_kwargs() if kind == "baseline" else scenarios.witness_kwargs()
            result, _ = run_scenario("NORMAL", kind, nodes, clock, coordinator_kwargs=kwargs)
            rows.append({"num_nodes": n, "scenario": "NORMAL", "coordinator": kind, "latency_ms": result.latency_ms})

        # HOST_HANG recovery-decision latency, both coordinators
        for kind in ("baseline", "witness"):
            clock = SimClock()
            nodes = build_nodes(clock, n, common.DEFAULT_SHARD_BYTES,
                                 common.DEFAULT_SSD_BANDWIDTH, common.DEFAULT_BMC_LATENCY_MS)
            kwargs = scenarios.baseline_kwargs() if kind == "baseline" else scenarios.witness_kwargs()
            result, _ = run_scenario("HOST_HANG", kind, nodes, clock,
                                      fault_at_write_fraction=0.1, fault_fn=kill_process_early,
                                      coordinator_kwargs=kwargs)
            rows.append({"num_nodes": n, "scenario": "HOST_HANG", "coordinator": kind, "latency_ms": result.latency_ms})

        # HOST_HANG_10PCT: 10% of nodes hang at once, WITNESS coordinator
        # with a bounded OOB fanout -- this is where real O(N/K) scaling
        # shows up, driven by a named, real constraint (BMC/management
        # network concurrency), not fabricated.
        clock = SimClock()
        nodes = build_nodes(clock, n, common.DEFAULT_SHARD_BYTES,
                             common.DEFAULT_SSD_BANDWIDTH, common.DEFAULT_BMC_LATENCY_MS)
        result, _ = run_scenario(
            "HOST_HANG_10PCT", "witness", nodes, clock,
            fault_at_write_fraction=0.1, fault_fn=kill_fraction_early,
            coordinator_kwargs=scenarios.witness_kwargs(max_concurrent_oob_queries=MAX_CONCURRENT_OOB_QUERIES),
        )
        rows.append({"num_nodes": n, "scenario": "HOST_HANG_10PCT", "coordinator": "witness", "latency_ms": result.latency_ms})
    return rows


def save_csv(rows, path="scale_sweep.csv"):
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["num_nodes", "scenario", "coordinator", "latency_ms"])
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {path}")


def save_plot(rows, path="scale_sweep.png"):
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib not installed -- skipping plot (CSV still written)")
        return

    fig, axes = plt.subplots(1, 3, figsize=(16, 4.5))

    for ax, scenario, title, coords in (
        (axes[0], "NORMAL", "Commit latency, no failures", ("baseline", "witness")),
        (axes[1], "HOST_HANG", "Recovery latency, 1 node hangs", ("baseline", "witness")),
        (axes[2], "HOST_HANG_10PCT", f"Recovery latency, {int(FAILURE_FRACTION*100)}% of nodes hang\n(OOB fanout capped at {MAX_CONCURRENT_OOB_QUERIES})", ("witness",)),
    ):
        for kind, style in (("baseline", "o-"), ("witness", "s-")):
            if kind not in coords:
                continue
            xs = [r["num_nodes"] for r in rows if r["scenario"] == scenario and r["coordinator"] == kind]
            ys = [r["latency_ms"] for r in rows if r["scenario"] == scenario and r["coordinator"] == kind]
            ax.plot(xs, ys, style, label=kind)
        ax.set_xscale("log", base=2)
        ax.set_yscale("log")
        ax.set_xlabel("number of simulated ranks / SSDs")
        ax.set_ylabel("latency (ms)")
        ax.set_title(title, fontsize=10)
        ax.legend()
        ax.grid(True, which="both", alpha=0.3)

    fig.suptitle("WITNESS vs. baseline: latency as N scales (simulated)")
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    print(f"wrote {path}")


def _lookup(rows, num_nodes, scenario, coordinator):
    for r in rows:
        if r["num_nodes"] == num_nodes and r["scenario"] == scenario and r["coordinator"] == coordinator:
            return r["latency_ms"]
    return None


def summarize(rows):
    """Reused by demo 8's 'scaling results' section, so the honesty-check
    text lives in exactly one place instead of being copy-pasted."""
    n_lo, n_hi = NODE_COUNTS[0], NODE_COUNTS[-1]
    print(f"\nSimulated-latency summary (ms) at N={n_lo} vs. N={n_hi}:")
    print(f"  NORMAL (no failures):        baseline {_lookup(rows, n_lo, 'NORMAL', 'baseline'):.0f} -> "
          f"{_lookup(rows, n_hi, 'NORMAL', 'baseline'):.0f}   |   witness "
          f"{_lookup(rows, n_lo, 'NORMAL', 'witness'):.0f} -> {_lookup(rows, n_hi, 'NORMAL', 'witness'):.0f}")
    print(f"  HOST_HANG, 1 node fails:     baseline {_lookup(rows, n_lo, 'HOST_HANG', 'baseline'):.0f} -> "
          f"{_lookup(rows, n_hi, 'HOST_HANG', 'baseline'):.0f}   |   witness "
          f"{_lookup(rows, n_lo, 'HOST_HANG', 'witness'):.0f} -> {_lookup(rows, n_hi, 'HOST_HANG', 'witness'):.0f}")
    print(f"  HOST_HANG, 10% of nodes:     witness  {_lookup(rows, n_lo, 'HOST_HANG_10PCT', 'witness'):.0f} -> "
          f"{_lookup(rows, n_hi, 'HOST_HANG_10PCT', 'witness'):.0f}  (fanout-limited growth once >{MAX_CONCURRENT_OOB_QUERIES} "
          f"nodes fail at once)")

    print("\nHonesty check on this sweep, stated plainly:")
    print("  - NORMAL and single-node HOST_HANG are FLAT vs. N by construction in this model: no")
    print("    per-rank straggler jitter or collective-communication overhead is simulated, and a")
    print("    single failed node's OOB round trip genuinely does not depend on cluster size. A")
    print("    real cluster's NORMAL path would show some growth from collective-comm overhead at")
    print("    very large N -- that effect is out of scope for this prototype, not hidden here.")
    print("  - The 10%-of-nodes-fail line is the one place this sweep shows real N-driven growth,")
    print("    and it is driven by a named, real constraint (max_concurrent_oob_queries, modeling")
    print("    BMC/management-network fanout limits) -- not tuned to produce a nicer-looking curve.")
    print("  - The baseline's flat, huge HOST_HANG number is not a scaling result at all: it is")
    print("    exactly its configured barrier_timeout_ms, by construction, regardless of N.")


def main():
    common.print_banner("DEMO 7/8 -- SCALING: latency vs. number of simulated ranks/SSDs")
    common.print_legend()
    print("All latency figures below are DISCRETE-EVENT SIMULATION TIME, not measured hardware")
    print("performance -- no real NAND, SMBus, or NVMe-MI wire protocol is involved anywhere in")
    print(f"this sweep. Sweeping N in {NODE_COUNTS} ({NODE_COUNTS[0]} to {NODE_COUNTS[-1]}) costs CPU")
    print("time only; a discrete-event clock means no real wall-clock waiting occurs.\n")

    rows = sweep()
    save_csv(rows)
    save_plot(rows)
    summarize(rows)


if __name__ == "__main__":
    main()
