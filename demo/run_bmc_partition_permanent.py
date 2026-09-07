"""
Demo 5/8 -- BMC_PARTITION_PERMANENT: same fault as demo 4, except the
BMC/management path never comes back.

The other half of the split-brain safeguard: WITNESS must not wait
forever either. Once the fencing window is genuinely exhausted, it must
reject -- but only after exhausting retries, never on the first
unreachable response.
"""

import demo._common as common
import demo.scenarios as scenarios


def narrator(prefix):
    def _emit(t_ms, msg):
        print(f"  [{prefix:8s} t={t_ms:9.1f}ms] {msg}")
    return _emit


def main():
    common.print_banner("DEMO 5/8 -- BMC_PARTITION_PERMANENT: OOB path never recovers")
    common.print_legend()

    print(f"Fault: rank-0's host hangs AND its BMC/management path goes unreachable --")
    print(f"       and stays that way for the rest of the run.\n")

    result, commit_log, nodes = scenarios.run_bmc_partition_permanent(on_event=narrator("WITNESS"))

    print(f"\nverdict={result.verdict}  latency={common.fmt_ms(result.latency_ms)}  "
          f"committed generation={commit_log.latest_committed()}")
    print(f"\nCorrect behavior: rejected only AFTER the {common.WITNESS_FENCING_TIMEOUT_MS/1000:.0f}s fencing window")
    print("was exhausted -- a bounded, deliberate wait, not an immediate guess.")


if __name__ == "__main__":
    main()
