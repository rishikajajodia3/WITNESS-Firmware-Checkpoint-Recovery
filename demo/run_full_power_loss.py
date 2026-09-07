"""
Demo 6/8 -- FULL_POWER_LOSS: the entire chassis goes dark -- host,
SSD, and BMC all lose power together.

This is the scenario WITNESS explicitly does NOT claim to help with,
demonstrated on purpose. The whole point of Scenario B is that the SSD
and BMC stay powered while the host doesn't; when that assumption is
false, the out-of-band path is dark too, and WITNESS degrades to the
same conservative behavior as the baseline. Showing this is part of
being honest about scope, not a bug.
"""

import demo._common as common
import demo.scenarios as scenarios


def main():
    common.print_banner("DEMO 6/8 -- FULL_POWER_LOSS: out of scope by design")
    common.print_legend()

    print("Fault: rank-0's host, SSD, AND BMC all lose power together mid-write.")
    print("       WITNESS's out-of-band path requires the SSD+BMC to stay powered --")
    print("       that assumption is false here on purpose.\n")
    print(f"Both coordinators are configured with the SAME {common.fmt_ms(scenarios.FULL_POWER_LOSS_TIMEOUT_MS)} "
          f"timeout for this scenario, on purpose:")
    print("WITNESS has NO information advantage here BECAUSE the SSD and BMC are also powered")
    print("off, not just the host -- its out-of-band path needs SSD+BMC power to work at all, so")
    print("there is nothing legitimate to compare a shorter WITNESS-specific timeout against.\n")

    results = {}
    for kind in ("baseline", "witness"):
        result, commit_log, nodes = scenarios.run_full_power_loss(kind)
        disk_truth = nodes[0].ssd.get_seal_status(1)
        results[kind] = result
        print(f"[{kind.upper():8s}] verdict={result.verdict:9s} "
              f"latency={common.fmt_ms(result.latency_ms):>10s}  rank-0 disk state: {disk_truth}")

    gap_ms = abs(results["witness"].latency_ms - results["baseline"].latency_ms)
    print(f"\nBoth REJECTED, from the SAME configured timeout. The remaining ~{common.fmt_ms(gap_ms)} gap is")
    print("just per-poll/round-trip bookkeeping overhead (WITNESS's oob_poll_interval_ms and one")
    print("extra OOB query attempt before fencing starts) -- not a capability difference. WITNESS")
    print("claims NO advantage here -- this is the honest boundary of the frozen scope, not a")
    print("broader claim about surviving arbitrary power loss.")


if __name__ == "__main__":
    main()
