"""
Two commit paths over the same simulated hardware, differing only in how
they determine shard completion:

BaselineCoordinator models a PyTorch-DCP-style collective barrier: a
shard only counts as done when the training process itself acknowledges
it over the same in-band channel used for training. A dead process
cannot acknowledge, so the coordinator can only wait out a fixed timeout
and then conservatively reject the whole generation -- even if the data
in fact landed safely before the process died.

WitnessCoordinator asks the process first (fast path, covers Scenario A
for free) and falls back to the simulated BMC/NVMe-MI out-of-band query
when the process is unavailable. An unreachable BMC is treated as
UNKNOWN and retried within a fencing window -- never silently treated
as proof the shard failed. A reachable device answering "not sealed
yet" is not a failure signal either (a device can't distinguish
"still writing" from "done" any other way) -- rejection only happens
when a per-node fencing window (persistent unreachability) or the
coordinator's own outer round timeout is exhausted.
"""

from typing import Callable

from witness.clock import SimClock
from witness.ftl import SealStatus
from witness.node import Node


class CommitLog:
    """The global commit record. In production this would be replicated
    across a small quorum (a few devices, or an external KV store) so the
    coordinator isn't a new single point of failure -- this simulation
    keeps a single in-memory log and treats that replication as a stated,
    not-yet-built requirement (see docs/ARCHITECTURE.md)."""

    def __init__(self):
        self._committed: list[int] = []

    def commit(self, generation: int) -> None:
        self._committed.append(generation)

    def latest_committed(self) -> int | None:
        """O(1) recovery lookup: no re-read of checkpoint data required."""
        return self._committed[-1] if self._committed else None

    def metadata_bytes(self) -> int:
        return len(self._committed) * 4  # one uint32 generation id per entry


class BaselineCoordinator:
    def __init__(self, clock: SimClock, nodes: list[Node], barrier_timeout_ms: float = 300_000, commit_log: CommitLog | None = None):
        self.clock = clock
        self.nodes = nodes
        self.barrier_timeout_ms = barrier_timeout_ms
        self.commit_log = commit_log or CommitLog()

    def run_checkpoint(
        self,
        generation: int,
        on_result: Callable[[bool, float], None],
        on_event: Callable[[float, str], None] | None = None,
    ) -> None:
        """on_event is a pure narration hook for demos -- it reports only
        what this coordinator actually observes (in-band acks, and the
        eventual timeout). It changes no decision logic; omit it and
        behavior is identical."""

        def emit(msg: str):
            if on_event:
                on_event(self.clock.now(), msg)

        start = self.clock.now()
        acked: set[str] = set()
        state = {"done": False}

        emit(f"checkpoint generation {generation} started: {len(self.nodes)} rank(s) writing, "
             f"collective barrier will wait up to {self.barrier_timeout_ms:.0f} ms for every rank to ack")

        def node_ack(node_id: str):
            if state["done"]:
                return
            acked.add(node_id)
            emit(f"{node_id}: in-band barrier ack received ({len(acked)}/{len(self.nodes)})")
            if len(acked) == len(self.nodes):
                state["done"] = True
                self.commit_log.commit(generation)
                on_result(True, self.clock.now() - start)

        for n in self.nodes:
            def on_done(n=n):
                if n.process_alive:
                    node_ack(n.node_id)
                # a dead process simply never acks -- the barrier has no other
                # way to learn what its drive holds, and deliberately has no
                # visibility to report that here.

            n.start_checkpoint_write(generation, on_local_done=on_done)

        def on_timeout():
            if not state["done"]:
                missing = len(self.nodes) - len(acked)
                emit(f"barrier timeout of {self.barrier_timeout_ms:.0f} ms exhausted -- "
                     f"{missing} rank(s) never acked; rejecting generation {generation} "
                     f"(no way to know whether their data actually landed)")
                state["done"] = True
                on_result(False, self.clock.now() - start)

        self.clock.schedule(self.barrier_timeout_ms, on_timeout)


class WitnessCoordinator:
    def __init__(
        self,
        clock: SimClock,
        nodes: list[Node],
        oob_poll_interval_ms: float = 100.0,
        fencing_timeout_ms: float = 5_000.0,
        round_timeout_ms: float = 30_000.0,
        max_concurrent_oob_queries: int | None = None,
        commit_log: CommitLog | None = None,
    ):
        self.clock = clock
        self.nodes = nodes
        self.oob_poll_interval_ms = oob_poll_interval_ms
        # A real BMC/management network can't fan out unlimited simultaneous
        # queries. None = unlimited (fine for a handful of concurrent
        # failures); set this to model a fleet-wide fanout limit when many
        # nodes hang at once.
        self.max_concurrent_oob_queries = max_concurrent_oob_queries
        self._oob_in_flight = 0
        self._oob_queue: list[Callable[[], None]] = []
        # fencing_timeout_ms: how long a single node is allowed to stay an
        # UNKNOWN (unreachable) before that specifically counts against it.
        self.fencing_timeout_ms = fencing_timeout_ms
        # round_timeout_ms: an outer safety ceiling for the whole checkpoint
        # round (covers a device that is reachable and honestly still
        # writing, forever). Deliberately much smaller than a barrier
        # timeout, since a healthy device resolves in ~1 poll interval and
        # this only exists as a backstop, not the primary decision path.
        self.round_timeout_ms = round_timeout_ms
        self.commit_log = commit_log or CommitLog()

    def run_checkpoint(
        self,
        generation: int,
        on_result: Callable[[bool, float], None],
        on_event: Callable[[float, str], None] | None = None,
    ) -> None:
        """on_event is a pure narration hook for demos -- it reports the
        same state transitions the coordinator's own logic already acts
        on (in-band ack, in-band unavailable -> OOB fallback, OOB result,
        fencing). It changes no decision logic; omit it and behavior is
        identical. Each transition is reported once per node, not on
        every poll tick."""

        def emit(msg: str):
            if on_event:
                on_event(self.clock.now(), msg)

        start = self.clock.now()
        emit(f"checkpoint generation {generation} started: {len(self.nodes)} rank(s) writing, "
             f"tagged via FDP-style placement hints")
        for n in self.nodes:
            n.start_checkpoint_write(generation, on_local_done=None)

        node_state: dict[str, str] = {n.node_id: "PENDING" for n in self.nodes}  # PENDING / SEALED / REJECTED
        first_unknown_at: dict[str, float] = {}
        announced: set[tuple[str, str]] = set()  # (node_id, event_kind) already emitted once
        finished = {"done": False}

        def announce_once(node_id: str, kind: str, msg: str):
            key = (node_id, kind)
            if key not in announced:
                announced.add(key)
                emit(msg)

        def evaluate() -> bool:
            if finished["done"]:
                return True
            if all(v == "SEALED" for v in node_state.values()):
                finished["done"] = True
                self.commit_log.commit(generation)
                emit(f"all {len(self.nodes)} rank(s) confirmed sealed -- committing generation {generation}")
                on_result(True, self.clock.now() - start)
                return True
            if any(v == "REJECTED" for v in node_state.values()):
                finished["done"] = True
                emit(f"rejecting generation {generation}: at least one rank's shard could not be confirmed")
                on_result(False, self.clock.now() - start)
                return True
            return False

        def on_round_timeout():
            # Outer safety net only -- not the mechanism this design relies
            # on. If we get here, some node was reachable and honestly still
            # not sealed (or genuinely unreachable but under the per-node
            # fencing bound) for the entire round.
            if not finished["done"]:
                emit(f"outer round timeout of {self.round_timeout_ms:.0f} ms exhausted -- rejecting generation {generation}")
                finished["done"] = True
                on_result(False, self.clock.now() - start)

        self.clock.schedule(self.round_timeout_ms, on_round_timeout)

        def poll_node(n: Node):
            if finished["done"] or node_state[n.node_id] == "SEALED":
                return

            def handle_inband():
                if finished["done"]:
                    return
                status = n.report_status_inband(generation)
                if status is None:
                    announce_once(n.node_id, "process_dead",
                                  f"{n.node_id}: host process unreachable in-band -- "
                                  f"falling back to the simulated BMC/NVMe-MI out-of-band path")
                    query_oob(n)  # process is dead -- fall back to the OOB path
                    return
                if status == SealStatus.SEALED:
                    node_state[n.node_id] = "SEALED"
                    announce_once(n.node_id, "sealed_inband", f"{n.node_id}: in-band report -- generation sealed")
                if not evaluate():
                    self.clock.schedule(self.oob_poll_interval_ms, lambda: poll_node(n))

            self.clock.schedule(0, handle_inband)

        def query_oob(n: Node):
            def handle(status: SealStatus | None):
                if finished["done"]:
                    return
                if status == SealStatus.SEALED:
                    node_state[n.node_id] = "SEALED"
                    announce_once(n.node_id, "sealed_oob",
                                  f"{n.node_id}: BMC -> NVMe-MI query (real transport) for the proposed "
                                  f"checkpoint-seal log page -> SEALED (confirmed WITHOUT the host)")
                elif status == SealStatus.NOT_SEALED:
                    # A reachable device honestly still writing. This is NOT
                    # a failure signal -- "not sealed yet" and "failed" are
                    # different facts, and only the device itself can tell
                    # them apart. Keep polling; the round_timeout backstop
                    # bounds how long this can go on.
                    node_state[n.node_id] = "PENDING"
                else:
                    # BMC unreachable or SSD unpowered: UNKNOWN. Never treated
                    # as a rejection until the fencing window is exhausted.
                    node_state[n.node_id] = "PENDING"
                    is_first = n.node_id not in first_unknown_at
                    t0 = first_unknown_at.setdefault(n.node_id, self.clock.now())
                    if is_first:
                        announce_once(n.node_id, "unreachable",
                                      f"{n.node_id}: BMC/management path unreachable -- "
                                      f"entering fencing window ({self.fencing_timeout_ms:.0f} ms) instead of "
                                      f"treating this as proof of failure")
                    if self.clock.now() - t0 >= self.fencing_timeout_ms:
                        node_state[n.node_id] = "REJECTED"
                        announce_once(n.node_id, "fenced_out",
                                      f"{n.node_id}: fencing window exhausted -- treating as failed")
                if not evaluate():
                    self.clock.schedule(self.oob_poll_interval_ms, lambda: poll_node(n))

            self._issue_oob(n, generation, handle)

        for n in self.nodes:
            poll_node(n)

    def _issue_oob(self, n: Node, generation: int, handle: Callable[[SealStatus | None], None]) -> None:
        """Gate OOB queries through max_concurrent_oob_queries, if set. This
        is what lets the scale sweep exhibit real fanout-limited scaling
        when many nodes need the OOB path simultaneously, instead of an
        (unrealistic) assumption of infinite parallel BMC bandwidth."""

        def _run():
            self._oob_in_flight += 1

            def wrapped(status: SealStatus | None):
                self._oob_in_flight -= 1
                if self._oob_queue:
                    nxt = self._oob_queue.pop(0)
                    self.clock.schedule(0, nxt)
                handle(status)

            n.bmc.query_sealed(generation, wrapped)

        if self.max_concurrent_oob_queries is None or self._oob_in_flight < self.max_concurrent_oob_queries:
            _run()
        else:
            self._oob_queue.append(_run)
