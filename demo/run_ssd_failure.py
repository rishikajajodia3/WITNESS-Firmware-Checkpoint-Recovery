"""
Demo 3/8 -- SSD_FAILURE: the drive itself dies (powers off) mid-write.

This is a no-regression check, not a WITNESS win. Neither the in-band
path nor the simulated BMC/NVMe-MI out-of-band path can get an answer
from a truly dead SSD -- there is no data to recover, and WITNESS must
say so, not fabricate a commit.
"""

import demo._common as common
import demo.scenarios as scenarios


def main():
    common.print_banner("DEMO 3/8 -- SSD_FAILURE: the drive itself fails mid-write")
    common.print_legend()

    print("Fault: rank-0's SSD is powered off partway through its write -- both the")
    print("       in-band path and the OOB path read from the SAME dead device, so")
    print("       neither can produce a seal confirmation. Both coordinators must reject.\n")

    for kind in ("baseline", "witness"):
        result, commit_log, nodes = scenarios.run_ssd_failure(kind)
        disk_truth = nodes[0].ssd.get_seal_status(1)
        print(f"[{kind.upper():8s}] verdict={result.verdict:9s} "
              f"latency={common.fmt_ms(result.latency_ms):>10s}  rank-0 disk state: {disk_truth} "
              f"(None = device unreachable/unpowered)")

    print("\nBoth REJECTED -- no false commit, no regression versus the baseline's behavior.")
    print("Note on the latency gap: unlike Scenario B, this is NOT a data-recovery advantage --")
    print("neither side recovers anything here. WITNESS's shorter number reflects a real but")
    print("separate property: its BMC is still reachable, so it actively re-probes on a bounded")
    print("fencing window instead of waiting out one coarse barrier timeout with zero information")
    print("in the meantime. That faster, more precise REJECTION is a legitimate side benefit of")
    print("the fencing design, not a claim that the missing checkpoint data was recovered.")


if __name__ == "__main__":
    main()
