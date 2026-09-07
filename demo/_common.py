"""Shared setup so every demo script can be run directly with `python demo/foo.py`."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DEFAULT_NUM_NODES = 8
DEFAULT_SHARD_BYTES = 10_000_000  # 10 MB per shard, illustrative
DEFAULT_SSD_BANDWIDTH = 50_000  # bytes/ms == 50 GB/s, illustrative
DEFAULT_BMC_LATENCY_MS = 15.0

BASELINE_BARRIER_TIMEOUT_MS = 300_000.0  # 5 min, a realistic torchrun-style default
WITNESS_POLL_INTERVAL_MS = 100.0
WITNESS_FENCING_TIMEOUT_MS = 5_000.0
WITNESS_ROUND_TIMEOUT_MS = 30_000.0


def fmt_ms(ms: float) -> str:
    if ms >= 1000:
        return f"{ms / 1000:.2f} s"
    return f"{ms:.1f} ms"


def print_legend():
    """Printed at the top of every demo so the audience never has to guess
    what's real, what's proposed, and what's simulated. NVMe-MI itself is a
    real, ratified transport; the checkpoint-seal PAYLOAD carried over it is
    this project's proposal, not an existing NVMe feature -- keep those two
    facts visibly separate everywhere, including here."""
    print("-" * 78)
    print("REAL EXISTING STANDARD (ratified, shipping today):")
    print("  NVMe Flexible Data Placement -- per-checkpoint write tagging")
    print("  NVMe-MI -- the out-of-band TRANSPORT (Admin-command/log-page queries")
    print("             to a drive controller). Real and already on shipping")
    print("             enterprise SSDs. What rides over it below is NOT standard.")
    print("  UBM / SMBus sideband -- drive bay -> BMC, independent of host CPU/OS")
    print("  BMC -- a separately powered, independently reachable service processor")
    print()
    print("OUR PROPOSED FIRMWARE BEHAVIOR (rides on top of real NVMe-MI; the")
    print("PAYLOAD below does not exist in any shipping SSD -- only the transport does):")
    print('  a vendor-specific "checkpoint generation sealed" log page')
    print()
    print("SIMULATED COMPONENT (no real hardware or wire protocol touched here):")
    print("  SSD/FTL write timing, BMC relay latency + reachability, NAND behavior")
    print("-" * 78)
    print()


def print_banner(title: str):
    print("=" * 78)
    print(title)
    print("=" * 78)
    print()
