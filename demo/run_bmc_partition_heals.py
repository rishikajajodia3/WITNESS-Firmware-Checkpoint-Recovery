"""
Demo 4/8 -- BMC_PARTITION_HEALS: the host hangs AND its BMC/management
network goes unreachable too -- but the partition heals before the
fencing window expires.

This exercises the split-brain safeguard: an unreachable BMC must be
treated as UNKNOWN, never as proof the shard failed, and retried within
the fencing window. If the partition resolves in time, WITNESS must
still commit correctly -- it must not have "given up" on a bare timeout.
"""

import demo._common as common
import demo.scenarios as scenarios

HEAL_AFTER_MS = 1_000.0


def narrator(prefix):
    def _emit(t_ms, msg):
        print(f"  [{prefix:8s} t={t_ms:9.1f}ms] {msg}")
    return _emit


def main():
    common.print_banner("DEMO 4/8 -- BMC_PARTITION_HEALS: OOB path unreachable, then recovers")
    common.print_legend()

    print(f"Fault: rank-0's host hangs AND its BMC/management path goes unreachable at the")
    print(f"       same moment. The partition heals after {HEAL_AFTER_MS/1000:.1f}s -- well inside the "
          f"{common.WITNESS_FENCING_TIMEOUT_MS/1000:.0f}s fencing window.\n")

    result, commit_log, nodes = scenarios.run_bmc_partition_heals(
        heal_after_ms=HEAL_AFTER_MS, on_event=narrator("WITNESS")
    )

    print(f"\nverdict={result.verdict}  latency={common.fmt_ms(result.latency_ms)}  "
          f"committed generation={commit_log.latest_committed()}")
    print("\nCorrect behavior: WITNESS kept retrying through the fencing window instead of")
    print("rejecting on the first failed query, and committed once the path recovered.")


if __name__ == "__main__":
    main()
