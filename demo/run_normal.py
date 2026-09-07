"""
Demo 1/8 -- NORMAL: no failures.

Checkpoint G starts -> every rank writes its shard, tagged by generation
(FDP-style) -> each SSD seals G once its shard is fully durable -> the
coordinator verifies all ranks -> G commits. Both coordinators should
behave identically here; this is the sanity check before the failure
demos, not the interesting case.
"""

import demo._common as common
import demo.scenarios as scenarios


def main():
    common.print_banner("DEMO 1/8 -- NORMAL: successful checkpoint, no failures")
    common.print_legend()

    for kind in ("baseline", "witness"):
        result, commit_log, _ = scenarios.run_normal(kind)
        print(f"[{kind.upper():8s}] verdict={result.verdict:9s} "
              f"commit_latency={common.fmt_ms(result.latency_ms):>10s}  "
              f"latest_committed_generation={commit_log.latest_committed()}")


if __name__ == "__main__":
    main()
