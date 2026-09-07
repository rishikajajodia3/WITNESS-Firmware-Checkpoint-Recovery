"""
WITNESS dashboard backend.

This is purely an integration layer: every endpoint here calls straight into
the ALREADY-VERIFIED demo.scenarios / witness simulation code and serializes
whatever comes back. It adds no new coordinator logic, no new FTL logic, no
new NVMe-MI logic, and does not alter any file under witness/ or demo/.

Run with:
    python -m uvicorn dashboard.backend.app:app --reload --port 8000
(from the repository root)
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

import demo._common as common
import demo.scenarios as scenarios
from dashboard.backend.serialize import serialize_run

app = FastAPI(title="WITNESS Dashboard API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

SCENARIOS = [
    {
        "id": "normal",
        "name": "Normal Operation",
        "short": "No faults injected. Sanity check that both coordinators commit identically.",
        "description": "Every rank writes its shard and acknowledges normally. Both the baseline "
                        "and WITNESS coordinators should reach the same COMMITTED verdict at the "
                        "same simulated latency -- WITNESS gets no advantage when nothing fails.",
        "has_baseline": True,
    },
    {
        "id": "host_hang",
        "name": "Host OS / CPU Hang",
        "short": "The central WITNESS scenario: the training host hangs, SSD + BMC stay powered.",
        "description": "rank-0's host OS/CPU hangs before its checkpoint write completes. Its SSD "
                        "keeps writing regardless -- the write timer is independent of the host "
                        "process -- and its BMC stays powered and reachable. The baseline has no "
                        "channel left to ask; WITNESS falls back to the BMC/NVMe-MI out-of-band path.",
        "has_baseline": True,
    },
    {
        "id": "ssd_failure",
        "name": "SSD Failure",
        "short": "The drive itself dies mid-write. No regression check, not a WITNESS win.",
        "description": "rank-0's SSD is powered off partway through its write. Both the in-band and "
                        "out-of-band paths read from the SAME dead device, so neither can produce a "
                        "seal confirmation. Both coordinators correctly REJECT -- there is no data "
                        "to recover.",
        "has_baseline": True,
    },
    {
        "id": "bmc_partition_heals",
        "name": "Temporary BMC Partition",
        "short": "The management path itself drops, then heals inside the fencing window.",
        "description": "rank-0's host hangs AND its BMC/management path goes unreachable at the same "
                        "moment -- but the partition heals well inside the fencing window. WITNESS "
                        "keeps retrying instead of rejecting on the first failed query, and commits "
                        "once the path recovers.",
        "has_baseline": False,
    },
    {
        "id": "bmc_partition_permanent",
        "name": "Permanent BMC Partition",
        "short": "Same fault, but the management path never comes back.",
        "description": "Same as the temporary partition, except the BMC/management path never heals. "
                        "WITNESS rejects only after the fencing window is genuinely exhausted -- a "
                        "bounded, deliberate wait, never an immediate guess.",
        "has_baseline": False,
    },
    {
        "id": "full_power_loss",
        "name": "Full Power Loss",
        "short": "The scope boundary, shown on purpose: WITNESS claims no advantage here.",
        "description": "rank-0's host, SSD, AND BMC all lose power together. WITNESS's out-of-band "
                        "path needs the SSD and BMC to stay powered -- that assumption is false here, "
                        "so WITNESS degrades to the same conservative behavior as the baseline.",
        "has_baseline": True,
    },
]

_SCENARIO_IDS = {s["id"] for s in SCENARIOS}


def _run_with_kind(fn, coordinator_kind, num_nodes):
    events: list[dict] = []
    result, commit_log, nodes = fn(
        coordinator_kind, num_nodes=num_nodes,
        on_event=lambda t_ms, msg: events.append({"t_ms": t_ms, "message": msg}),
    )
    return serialize_run(coordinator_kind, result, commit_log, nodes, events)


def _run_witness_only(fn, num_nodes, **kwargs):
    events: list[dict] = []
    result, commit_log, nodes = fn(
        num_nodes=num_nodes,
        on_event=lambda t_ms, msg: events.append({"t_ms": t_ms, "message": msg}),
        **kwargs,
    )
    return serialize_run("witness", result, commit_log, nodes, events)


def _run_scenario(scenario_id: str, num_nodes: int) -> dict:
    if scenario_id == "normal":
        runs = {k: _run_with_kind(scenarios.run_normal, k, num_nodes) for k in ("baseline", "witness")}
    elif scenario_id == "host_hang":
        runs = {k: _run_with_kind(scenarios.run_host_hang, k, num_nodes) for k in ("baseline", "witness")}
    elif scenario_id == "ssd_failure":
        runs = {k: _run_with_kind(scenarios.run_ssd_failure, k, num_nodes) for k in ("baseline", "witness")}
    elif scenario_id == "full_power_loss":
        runs = {k: _run_with_kind(scenarios.run_full_power_loss, k, num_nodes) for k in ("baseline", "witness")}
    elif scenario_id == "bmc_partition_heals":
        runs = {"witness": _run_witness_only(scenarios.run_bmc_partition_heals, num_nodes)}
    elif scenario_id == "bmc_partition_permanent":
        runs = {"witness": _run_witness_only(scenarios.run_bmc_partition_permanent, num_nodes)}
    else:
        raise HTTPException(status_code=404, detail=f"unknown scenario '{scenario_id}'")

    speedup = None
    if "baseline" in runs and "witness" in runs:
        b, w = runs["baseline"]["latency_ms"], runs["witness"]["latency_ms"]
        if w > 0:
            speedup = b / w

    return {
        "scenario": scenario_id,
        "num_nodes": num_nodes,
        "runs": runs,
        "speedup_x": speedup,
        "simulated": True,  # every latency figure here is discrete-event simulation time
    }


@app.get("/api/health")
def health():
    return {"status": "ONLINE", "build": "CheckpointLedger"}


@app.get("/api/scenarios")
def get_scenarios():
    return SCENARIOS


@app.get("/api/run/{scenario_id}")
def run_scenario_get(scenario_id: str, num_nodes: int = common.DEFAULT_NUM_NODES):
    if scenario_id not in _SCENARIO_IDS:
        raise HTTPException(status_code=404, detail=f"unknown scenario '{scenario_id}'")
    return _run_scenario(scenario_id, num_nodes)


@app.post("/api/run/{scenario_id}")
def run_scenario_post(scenario_id: str, num_nodes: int = common.DEFAULT_NUM_NODES):
    if scenario_id not in _SCENARIO_IDS:
        raise HTTPException(status_code=404, detail=f"unknown scenario '{scenario_id}'")
    return _run_scenario(scenario_id, num_nodes)


@app.get("/api/architecture")
def get_architecture():
    """Static real-vs-proposed-vs-simulated breakdown, transcribed verbatim
    from docs/ARCHITECTURE.md so the dashboard cannot drift from the
    project's own frozen scope statement."""
    return {
        "real": [
            {"name": "NVMe Flexible Data Placement (FDP)", "detail": "Per-checkpoint write tagging. Real, ratified standard (NVMe 2.0+)."},
            {"name": "NVMe-MI", "detail": "Out-of-band Admin-command/log-page transport to a drive controller. Real and shipping on enterprise SSDs today -- only the TRANSPORT, not the checkpoint payload."},
            {"name": "UBM / SMBus sideband", "detail": "Drive bay to BMC, independent of host CPU/OS. Standard OCP-class backplane management practice."},
            {"name": "BMC", "detail": "A separately powered, independently reachable service processor -- the same one used to remote power-cycle a hung node today."},
        ],
        "proposed": [
            {"name": "Checkpoint-generation tracking in SSD firmware", "detail": "Remembering per-generation completeness as first-class firmware state."},
            {"name": "SEALED state", "detail": "The durable, externally-queryable signal that a generation's shard is fully written."},
            {"name": "Checkpoint-seal log page", "detail": "A vendor-specific NVMe-MI log page carrying that SEALED state. No commercial SSD defines this payload today."},
            {"name": "Coordinator integration", "detail": "WitnessCoordinator's in-band-first, OOB-fallback, fencing-window decision logic."},
        ],
        "simulated": [
            {"name": "NAND / FTL timing", "detail": "A simplified bandwidth-delay write model, not a NAND/GC/wear-leveling simulator."},
            {"name": "BMC reachability", "detail": "Modeled as an independent, host-process-independent boolean channel, not real SMBus/I2C wire signaling."},
            {"name": "NVMe-MI transport behavior", "detail": "The Get-Log-Page round trip is modeled with a configurable latency, not measured on real silicon."},
            {"name": "SSD durability timing", "detail": "Write completion is a bandwidth-delay calculation, not observed hardware."},
        ],
    }


FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
