"""Result objects and metadata-overhead accounting."""

from dataclasses import dataclass

from witness.coordinator import CommitLog
from witness.nvme_mi import LOG_PAGE_SIZE_BYTES

# Illustrative constant, not a benchmarked number: a PyTorch-DCP-style
# .metadata entry (shard filename, byte range, checksum) per rank.
# Used only to give the metadata-overhead comparison a concrete shape;
# labeled as illustrative wherever it's reported.
BASELINE_MANIFEST_BYTES_PER_SHARD = 128
BASELINE_MANIFEST_HEADER_BYTES = 32


@dataclass
class RunResult:
    scenario: str
    coordinator: str
    num_nodes: int
    committed: bool
    generation: int
    latency_ms: float

    @property
    def verdict(self) -> str:
        return "COMMITTED" if self.committed else "REJECTED"


def baseline_metadata_bytes(num_nodes: int) -> int:
    return num_nodes * BASELINE_MANIFEST_BYTES_PER_SHARD + BASELINE_MANIFEST_HEADER_BYTES


def witness_metadata_bytes(num_nodes: int, commit_log: CommitLog) -> int:
    per_shard_log_pages = num_nodes * LOG_PAGE_SIZE_BYTES
    return per_shard_log_pages + commit_log.metadata_bytes()
