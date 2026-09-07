/* WITNESS dashboard frontend.
 * Pure presentation layer: every number rendered here comes from the
 * FastAPI backend, which in turn comes from the real, unmodified WITNESS
 * simulation (demo.scenarios / witness.*). Nothing in this file computes a
 * verdict, a latency, or a seal status -- it only formats and animates
 * values the backend returned.
 */

const API = "";

async function getJSON(url) {
  const res = await fetch(API + url);
  if (!res.ok) throw new Error(`${url} -> ${res.status}`);
  return res.json();
}

function fmtMs(ms) {
  if (ms == null) return "--";
  if (ms >= 1000) return (ms / 1000).toFixed(2) + " s";
  return ms.toFixed(1) + " ms";
}

function fmtSpeedup(x) {
  if (x == null) return "--";
  return "~" + x.toLocaleString(undefined, { maximumFractionDigits: 0 }) + "×";
}

/* ---------------------------------------------------------------------- */
/* Pipeline stages -- driven by the WITNESS coordinator's own event text  */
/* ---------------------------------------------------------------------- */

const STAGES = [
  { id: "training", name: "AI TRAINING", detail: "Job running", match: () => true },
  { id: "write", name: "CHECKPOINT WRITE", detail: "FDP-tagged shard writes",
    match: (msgs) => msgs.some(m => /checkpoint generation \d+ started/.test(m)) },
  { id: "host", name: "HOST OS / CPU", detail: "",
    match: (msgs) => msgs.some(m => /unreachable in-band|host process unreachable/.test(m)) },
  { id: "oob", name: "BMC → NVMe-MI", detail: "Out-of-band query",
    match: (msgs) => msgs.some(m => /falling back to the simulated BMC|BMC\/management path unreachable|BMC -> NVMe-MI/.test(m)) },
  { id: "sealed", name: "SSD SEALED", detail: "Proposed log page",
    match: (msgs) => msgs.some(m => /-> SEALED/.test(m)) },
  { id: "coordinator", name: "WITNESS COORDINATOR", detail: "Fencing / decision",
    match: (msgs) => msgs.some(m => /confirmed sealed|rejecting generation|fencing window exhausted/.test(m)) },
  { id: "commit", name: "CHECKPOINT COMMITTED", detail: "",
    match: (msgs) => msgs.some(m => /committing generation/.test(m)) },
];

function stageOutcome(events) {
  const msgs = events.map(e => e.message);
  const failed = msgs.some(m => /rejecting generation|fencing window exhausted/.test(m));
  const committed = msgs.some(m => /committing generation/.test(m));
  const states = STAGES.map(s => (s.match(msgs) ? "done" : "pending"));
  if (failed) {
    const ci = STAGES.findIndex(s => s.id === "commit");
    states[ci] = "fail";
  }
  return { states, committed, failed };
}

function renderPipeline(el, events, { animate } = { animate: false }) {
  el.innerHTML = "";
  const frag = document.createDocumentFragment();
  const finalStates = stageOutcome(events).states;
  STAGES.forEach((s, i) => {
    if (i > 0) {
      const c = document.createElement("div");
      c.className = "pipe-connector";
      c.dataset.idx = i;
      frag.appendChild(c);
    }
    const node = document.createElement("div");
    node.className = "pipe-stage state-pending";
    node.dataset.idx = i;
    node.innerHTML = `
      <div class="stage-icon">${i + 1}</div>
      <div class="stage-name">${s.name}</div>
      <div class="stage-detail">${s.detail}</div>`;
    frag.appendChild(node);
  });
  el.appendChild(frag);

  const stageEls = [...el.querySelectorAll(".pipe-stage")];
  const connEls = [...el.querySelectorAll(".pipe-connector")];

  const apply = (i, state) => {
    stageEls[i].classList.remove("state-pending", "state-active", "state-done", "state-fail");
    stageEls[i].classList.add("state-" + state);
    if (i > 0) {
      connEls[i - 1].classList.toggle("state-done", state === "done" || state === "active");
      connEls[i - 1].classList.toggle("state-fail", state === "fail");
    }
  };

  if (!animate) {
    finalStates.forEach((st, i) => apply(i, st === "pending" ? "pending" : st));
    return;
  }

  finalStates.forEach((_, i) => {
    setTimeout(() => {
      apply(i, "active");
      setTimeout(() => apply(i, finalStates[i] === "pending" ? "pending" : finalStates[i]), 260);
    }, i * 260);
  });
}

/* ---------------------------------------------------------------------- */
/* Host / storage / management panel                                      */
/* ---------------------------------------------------------------------- */

function renderHostPanel(el, node) {
  if (!node) { el.innerHTML = `<div class="event-empty">No run yet.</div>`; return; }
  const tile = (label, ok, state, sub) => `
    <div class="host-tile ${ok ? "ok" : "bad"}">
      <div class="tile-label">${label}</div>
      <div class="tile-state">${state}</div>
      <div class="tile-sub">${sub}</div>
    </div>`;
  el.innerHTML =
    tile("Host CPU / OS", node.host_alive, node.host_alive ? "ONLINE" : "UNREACHABLE", node.node_id) +
    tile("SSD (Storage)", node.ssd_powered, node.ssd_powered ? "ONLINE" : "POWERED OFF", node.seal_status) +
    tile("BMC (Mgmt Plane)", node.bmc_reachable, node.bmc_reachable ? "ONLINE" : "UNREACHABLE", "NVMe-MI path");
}

/* ---------------------------------------------------------------------- */
/* Shard grid                                                              */
/* ---------------------------------------------------------------------- */

function badgeClass(shardStatus) {
  if (shardStatus === "SEALED") return "badge-sealed";
  if (shardStatus === "FAILED") return "badge-failed";
  return "badge-unknown";
}

function renderShardGrid(el, run) {
  if (!run) { el.innerHTML = `<div class="event-empty">No run yet.</div>`; return; }
  el.innerHTML = run.nodes.map(n => `
    <div class="shard-card">
      <div class="shard-id">${n.node_id.toUpperCase()}</div>
      <div class="shard-gen">Generation ${run.generation}</div>
      <span class="badge ${badgeClass(n.shard_status)}">${n.shard_status}</span>
      <div class="shard-meta">
        <div class="shard-meta-row ${!n.host_alive ? "bad" : ""}"><span>Host</span><b>${n.host_alive ? "up" : "down"}</b></div>
        <div class="shard-meta-row ${!n.bmc_reachable ? "bad" : ""}"><span>BMC</span><b>${n.bmc_reachable ? "up" : "down"}</b></div>
      </div>
    </div>`).join("");
}

/* ---------------------------------------------------------------------- */
/* Donut (shard status)                                                    */
/* ---------------------------------------------------------------------- */

function renderDonut(el, counts) {
  if (!counts) { el.innerHTML = `<div class="event-empty">No run yet.</div>`; return; }
  const total = counts.SEALED + counts.UNKNOWN + counts.FAILED || 1;
  const colors = { SEALED: "#1655c9", UNKNOWN: "#8892a0", FAILED: "#b0281f" };
  const order = ["SEALED", "UNKNOWN", "FAILED"];
  const r = 46, c = 2 * Math.PI * r;
  let offset = 0;
  const segs = order.map(k => {
    const frac = counts[k] / total;
    const len = frac * c;
    const seg = `<circle cx="60" cy="60" r="${r}" fill="none" stroke="${colors[k]}" stroke-width="16"
      stroke-dasharray="${len} ${c - len}" stroke-dashoffset="${-offset}" transform="rotate(-90 60 60)"/>`;
    offset += len;
    return seg;
  }).join("");

  el.innerHTML = `
    <div class="donut-wrap">
      <svg width="120" height="120" viewBox="0 0 120 120">
        <circle cx="60" cy="60" r="${r}" fill="none" stroke="#eef0f3" stroke-width="16"/>
        ${segs}
        <text x="60" y="56" text-anchor="middle" font-size="20" font-weight="800" fill="#0b1f3a">${total}</text>
        <text x="60" y="72" text-anchor="middle" font-size="9" fill="#8892a0">SHARDS</text>
      </svg>
      <div class="donut-legend">
        <div class="legend-row"><span class="legend-swatch" style="background:${colors.SEALED}"></span>${counts.SEALED} SEALED</div>
        <div class="legend-row"><span class="legend-swatch" style="background:${colors.UNKNOWN}"></span>${counts.UNKNOWN} UNKNOWN</div>
        <div class="legend-row"><span class="legend-swatch" style="background:${colors.FAILED}"></span>${counts.FAILED} FAILED</div>
      </div>
    </div>`;
}

/* ---------------------------------------------------------------------- */
/* Baseline vs WITNESS comparison                                         */
/* ---------------------------------------------------------------------- */

function renderCompare(el, runs) {
  el.innerHTML = "";
  const order = ["baseline", "witness"];
  order.forEach(kind => {
    const r = runs[kind];
    if (!r) return;
    const col = document.createElement("div");
    col.className = "compare-col " + kind;
    const verdictBadge = r.committed ? "badge-committed" : "badge-rejected";
    col.innerHTML = `
      <div class="compare-head">${kind === "baseline" ? "BASELINE" : "WITNESS"}</div>
      <div class="compare-body">
        <div class="compare-row"><span class="k">Checkpoint state</span><span class="v">${r.nodes[0] ? r.nodes[0].seal_status : "--"}</span></div>
        <div class="compare-row"><span class="k">Decision</span><span class="badge ${verdictBadge}">${r.verdict}</span></div>
        <div class="compare-row"><span class="k">Simulated latency</span><span class="v mono">${fmtMs(r.latency_ms)}</span></div>
        <div class="compare-row"><span class="k">Committed generation</span><span class="v mono">${r.committed_generation ?? "None"}</span></div>
      </div>`;
    el.appendChild(col);
  });
}

/* This banner's wording is derived from the ACTUAL verdicts of the run, not
 * a fixed per-scenario string -- a scenario where both coordinators reject
 * must never be captioned as if WITNESS "recovered data," even when its
 * rejection latency happens to be numerically smaller. Only a real
 * baseline-rejects/WITNESS-commits outcome gets the recovery framing. */
function renderSpeedup(el, runData) {
  const b = runData.runs.baseline, w = runData.runs.witness;
  if (!b || !w || !runData.speedup_x) { el.style.display = "none"; el.classList.remove("neutral"); return; }

  const recovered = !b.committed && w.committed;
  const bothRejected = !b.committed && !w.committed;

  if (!recovered && !bothRejected) { el.style.display = "none"; el.classList.remove("neutral"); return; }

  el.style.display = "flex";
  el.classList.toggle("neutral", bothRejected);

  if (recovered) {
    el.innerHTML = `
      <div>
        <div class="speedup-figure">${fmtSpeedup(runData.speedup_x)} faster</div>
        <div class="speedup-caption">WITNESS reached its verdict this much faster than the baseline for this scenario, by discovering the SSD's already-durable SEALED state through the out-of-band path instead of waiting out the baseline's fixed timeout.</div>
      </div>
      <div class="disclaimer">Simulated discrete-event result &middot; not measured real-SSD hardware performance</div>`;
    return;
  }

  // bothRejected: never claim a recovery win. If the two latencies are
  // close (e.g. full power loss, where both use the same configured
  // timeout on purpose), say so plainly instead of forcing a "Nx faster"
  // framing onto a near-1x ratio.
  const nearParity = runData.speedup_x < 1.15 && runData.speedup_x > 0.85;
  const headline = nearParity ? "Both REJECTED: comparable latency" : "Both REJECTED: no data recovered";
  const body = nearParity
    ? "Both coordinators reject at essentially the same simulated latency here. WITNESS has no information advantage in this scenario, so there is nothing legitimate to compare a shorter WITNESS-specific timeout against."
    : `WITNESS still reached its rejection ${fmtSpeedup(runData.speedup_x)} faster here by actively re-probing a reachable BMC on a bounded fencing window, instead of waiting out one fixed barrier timeout with no information in the meantime. That is a faster, more precise REJECTION, not a recovery advantage; no data was recovered on either side.`;
  el.innerHTML = `
    <div>
      <div class="speedup-figure" style="font-size:21px;">${headline}</div>
      <div class="speedup-caption">${body}</div>
    </div>
    <div class="disclaimer">Simulated discrete-event result &middot; not measured real-SSD hardware performance</div>`;
}

/* ---------------------------------------------------------------------- */
/* Bar chart (log-scaled width, exact labeled values)                     */
/* ---------------------------------------------------------------------- */

function renderBarChart(el, runs) {
  const entries = ["baseline", "witness"].filter(k => runs[k]).map(k => ({ kind: k, ms: runs[k].latency_ms }));
  if (entries.length === 0) { el.innerHTML = `<div class="event-empty">No run yet.</div>`; return; }
  const logs = entries.map(e => Math.log10(e.ms + 1));
  const maxLog = Math.max(...logs, 1);
  el.innerHTML = entries.map(e => {
    const pct = Math.max(4, (Math.log10(e.ms + 1) / maxLog) * 100);
    return `
      <div class="bar-row">
        <div class="bar-label">${e.kind === "baseline" ? "Baseline" : "WITNESS"}</div>
        <div class="bar-track"><div class="bar-fill ${e.kind}" style="width:${pct}%"></div></div>
        <div class="bar-value">${fmtMs(e.ms)}</div>
      </div>`;
  }).join("") + `<div class="section-note">Bar width is log-scaled for readability; labeled values are the exact simulated latencies.</div>`;
}

/* ---------------------------------------------------------------------- */
/* Event log playback                                                     */
/* ---------------------------------------------------------------------- */

function tagFor(msg) {
  if (/rejecting generation|fencing window exhausted|unreachable/.test(msg)) return "tag-fault";
  if (/committing generation|-> SEALED/.test(msg)) return "tag-commit";
  return "";
}

function renderEventLog(el, runs, { animate } = { animate: false }) {
  el.innerHTML = "";
  const order = ["witness", "baseline"];
  const blocks = order.filter(k => runs[k]);
  if (blocks.length === 0) { el.innerHTML = `<div class="event-empty">No events yet. Run a scenario.</div>`; return; }

  blocks.forEach(kind => {
    const header = document.createElement("div");
    header.style.cssText = "padding:8px 16px; font-size:10.5px; font-weight:800; letter-spacing:.06em; color:#8892a0; background:#f7f8fa; border-bottom:1px solid var(--border);";
    header.textContent = (kind === "witness" ? "WITNESS" : "BASELINE") + " COORDINATOR";
    el.appendChild(header);

    const rows = runs[kind].events.map(ev => {
      const row = document.createElement("div");
      row.className = "event-row " + tagFor(ev.message);
      row.innerHTML = `<div class="t">t=${ev.t_ms.toFixed(1)}ms</div><div class="m">${ev.message}</div>`;
      return row;
    });

    if (!animate) {
      rows.forEach(r => { r.style.opacity = 1; r.style.transform = "none"; el.appendChild(r); });
    } else {
      rows.forEach((r, i) => {
        r.style.animationDelay = (i * 70) + "ms";
        el.appendChild(r);
      });
    }
  });
}

/* ---------------------------------------------------------------------- */
/* Status strip (overview)                                                 */
/* ---------------------------------------------------------------------- */

function renderStatusStrip(el, runData) {
  const w = runData.runs.witness;
  const cell = (label, value, sub, cls) => `
    <div class="status-cell">
      <div class="status-label">${label}</div>
      <div class="status-value ${cls || ""}">${value}</div>
      ${sub ? `<div class="status-sub">${sub}</div>` : ""}
    </div>`;
  el.innerHTML =
    cell("Checkpoint", "Generation " + w.generation, `${w.num_nodes} ranks`) +
    cell("Training", "Host OS / CPU failure", "rank-0 (simulated)", "") +
    cell("Storage", `${w.shard_counts.SEALED} / ${w.nodes.length} shards sealed`, "confirmed via OOB path", "blue") +
    cell("WITNESS", w.committed ? "RECOVERABLE" : "UNRECOVERABLE", w.verdict, w.committed ? "green" : "");
}

/* ---------------------------------------------------------------------- */
/* View wiring                                                             */
/* ---------------------------------------------------------------------- */

let infraScene = null;

function switchView(id) {
  document.querySelectorAll(".view").forEach(v => v.classList.toggle("active", v.id === "view-" + id));
  document.querySelectorAll(".navlink").forEach(n => n.classList.toggle("active", n.dataset.view === id));
  if (infraScene) {
    if (id === "infrastructure") { infraScene.resize(); infraScene.start(); }
    else infraScene.stop();
  }
}

document.querySelectorAll(".navlink").forEach(n => n.addEventListener("click", () => switchView(n.dataset.view)));

/* ---------------------------------------------------------------------- */
/* Overview page bootstrap                                                 */
/* ---------------------------------------------------------------------- */

async function loadOverview() {
  const data = await getJSON("/api/run/host_hang");
  renderStatusStrip(document.getElementById("statusStrip"), data);
  renderPipeline(document.getElementById("pipelineOverview"), data.runs.witness.events, { animate: true });
  renderHostPanel(document.getElementById("hostPanel"), data.runs.witness.nodes[0]);
  renderDonut(document.getElementById("donutWrap"), data.runs.witness.shard_counts);
  renderShardGrid(document.getElementById("shardGridOverview"), data.runs.witness);
  renderCompare(document.getElementById("compareGrid"), data.runs);
  renderSpeedup(document.getElementById("speedupBanner"), data);
  renderBarChart(document.getElementById("barChart"), data.runs);
  renderEventLog(document.getElementById("eventLogOverview"), data.runs, { animate: true });
}

/* ---------------------------------------------------------------------- */
/* Simulation page                                                         */
/* ---------------------------------------------------------------------- */

let SCENARIOS = [];
let selectedScenario = null;

function renderScenarioPicker() {
  const el = document.getElementById("scenarioPicker");
  el.innerHTML = SCENARIOS.map(s => `
    <div class="scenario-option ${s.id === selectedScenario ? "selected" : ""}" data-id="${s.id}">
      <div class="opt-title">${s.name}</div>
      <div class="opt-desc">${s.short}</div>
    </div>`).join("");
  el.querySelectorAll(".scenario-option").forEach(opt => {
    opt.addEventListener("click", () => {
      selectedScenario = opt.dataset.id;
      renderScenarioPicker();
      renderScenarioDetail();
    });
  });
}

function renderScenarioDetail() {
  const el = document.getElementById("scenarioDetail");
  const s = SCENARIOS.find(s => s.id === selectedScenario);
  if (!s) { el.style.display = "none"; return; }
  el.style.display = "block";
  el.innerHTML = `<div class="sd-title">${s.name}</div><div class="sd-body">${s.description}</div>`;
}

async function runSelectedScenario(targets) {
  if (!selectedScenario) return;
  const btns = targets.buttons;
  btns.forEach(b => b.disabled = true);
  if (targets.status) targets.status.textContent = "Running simulation...";
  if (targets.status) targets.status.classList.add("live");

  try {
    const data = await getJSON(`/api/run/${selectedScenario}`);
    targets.onResult(data);
  } finally {
    btns.forEach(b => b.disabled = false);
    if (targets.status) { targets.status.textContent = "Done."; targets.status.classList.remove("live"); }
  }
}

function paintSimulationResult(data) {
  const primary = data.runs.witness || data.runs.baseline;
  renderHostPanel(document.getElementById("hostPanelSim"), primary.nodes[0]);
  renderDonut(document.getElementById("donutWrapSim"), primary.shard_counts);
  renderShardGrid(document.getElementById("shardGridSim"), primary);
  renderCompare(document.getElementById("compareGridSim"), data.runs);
  renderSpeedup(document.getElementById("speedupBannerSim"), data);
  renderBarChart(document.getElementById("barChartSim"), data.runs);
  renderEventLog(document.getElementById("eventLogSim"), data.runs, { animate: true });
}

async function initSimulationPage() {
  SCENARIOS = await getJSON("/api/scenarios");
  selectedScenario = SCENARIOS[1].id; // default to the headline Scenario B
  renderScenarioPicker();
  renderScenarioDetail();

  document.getElementById("btnRun").addEventListener("click", () => {
    runSelectedScenario({
      buttons: [document.getElementById("btnRun")],
      status: document.getElementById("runStatus"),
      onResult: paintSimulationResult,
    });
  });
}

/* ---------------------------------------------------------------------- */
/* 3D Infrastructure page                                                  */
/*                                                                          */
/* This view renders the SAME run object every other view uses (the       */
/* backend's /api/run/{scenario} response). It fabricates no new verdicts */
/* or states -- WitnessScene3D only reads run.nodes[] / run.events[] and  */
/* decides when to reveal each node's already-known outcome.              */
/* ---------------------------------------------------------------------- */

let infraRunData = null;

function renderScene3DNodePanel(el, nodeData, nodeId, extra) {
  if (extra && extra.kind) {
    const run = extra.run;
    if (!run) { el.innerHTML = `<div class="event-empty">No run yet.</div>`; return; }
    const title = extra.kind === "coordinator" ? "WITNESS COORDINATOR" : "CHECKPOINT COMMIT LOG";
    const rows = extra.kind === "coordinator"
      ? [["Verdict", run.verdict, run.committed ? "ok" : "bad"],
         ["Simulated latency", fmtMs(run.latency_ms), "neutral"]]
      : [["Committed generation", run.committed_generation ?? "None", "neutral"],
         ["Verdict", run.verdict, run.committed ? "ok" : "bad"]];
    el.innerHTML = `<div class="np-title">${title}</div>` +
      rows.map(([k, v, cls]) => `<div class="np-row"><span class="np-k">${k}</span><span class="np-v ${cls}">${v}</span></div>`).join("");
    return;
  }
  if (!nodeData) {
    el.innerHTML = `<div class="event-empty">Click any host, SSD, or BMC block in the scene to inspect it.</div>`;
    return;
  }
  const gen = infraRunData && infraRunData.runs.witness ? infraRunData.runs.witness.generation : "--";
  const rows = [
    ["SSD status", nodeData.ssd_powered ? "ONLINE" : "OFFLINE", nodeData.ssd_powered ? "ok" : "bad"],
    ["Generation", gen, "neutral"],
    ["Seal state", nodeData.seal_status, nodeData.seal_status === "SEALED" ? "ok" : "neutral"],
    ["Shard status", nodeData.shard_status, nodeData.shard_status === "SEALED" ? "ok" : (nodeData.shard_status === "FAILED" ? "bad" : "neutral")],
    ["Management", "NVMe-MI", "neutral"],
    ["Host CPU / OS", nodeData.host_alive ? "ONLINE" : "UNREACHABLE", nodeData.host_alive ? "ok" : "bad"],
    ["BMC", nodeData.bmc_reachable ? "ONLINE" : "UNREACHABLE", nodeData.bmc_reachable ? "ok" : "bad"],
  ];
  el.innerHTML = `<div class="np-title">${nodeId.toUpperCase()}</div>` +
    rows.map(([k, v, cls]) => `<div class="np-row"><span class="np-k">${k}</span><span class="np-v ${cls}">${v}</span></div>`).join("");
}

function scene3dButtonsSetPlaying(playing) {
  document.getElementById("btnScene3dPlay").disabled = playing;
  document.getElementById("btnScene3dPause").disabled = !playing;
}

function scene3dSetStatus(step, index, total) {
  const el = document.getElementById("scene3dStepStatus");
  if (!el) return;
  if (!total) { el.textContent = "Select a scenario and press Run."; return; }
  if (index < 0) { el.textContent = `Ready -- ${total} recorded steps from this run. Press Play.`; return; }
  el.textContent = `Step ${index + 1}/${total}: ${step.message}`;
}

async function initInfrastructurePage() {
  const select = document.getElementById("scene3dScenario");
  select.innerHTML = SCENARIOS.map(s => `<option value="${s.id}">${s.name}</option>`).join("");
  select.value = "host_hang";

  infraScene = new WitnessScene3D(document.getElementById("scene3dCanvasWrap"), {
    stepDelayMs: 650,
    onNodeClick: (nodeData, nodeId, extra) => {
      renderScene3DNodePanel(document.getElementById("scene3dNodePanel"), nodeData, nodeId,
        extra ? { kind: extra.kind, run: infraRunData && infraRunData.runs.witness } : null);
    },
    onStep: scene3dSetStatus,
    onPlayState: (playing) => scene3dButtonsSetPlaying(playing),
  });

  if (!infraScene.available) {
    document.getElementById("scene3dCanvasWrap").style.display = "none";
    document.getElementById("scene3dFallback").style.display = "block";
  }

  async function runInfraScenario() {
    const id = select.value;
    const btn = document.getElementById("btnScene3dRun");
    btn.disabled = true;
    try {
      const data = await getJSON(`/api/run/${id}`);
      infraRunData = data;
      infraScene.loadRun(data);
      renderEventLog(document.getElementById("scene3dEventLog"), data.runs, { animate: false });
      renderScene3DNodePanel(document.getElementById("scene3dNodePanel"), null, null, null);
      if (!infraScene.available) {
        renderPipeline(document.getElementById("scene3dFallbackPipeline"), data.runs.witness.events, { animate: true });
      }
    } finally {
      btn.disabled = false;
    }
  }

  document.getElementById("btnScene3dRun").addEventListener("click", runInfraScenario);
  document.getElementById("btnScene3dPlay").addEventListener("click", () => infraScene.play());
  document.getElementById("btnScene3dPause").addEventListener("click", () => infraScene.pause());
  document.getElementById("btnScene3dReset").addEventListener("click", () => infraScene.reset());

  await runInfraScenario();
}

/* ---------------------------------------------------------------------- */
/* Architecture page                                                       */
/* ---------------------------------------------------------------------- */

async function loadArchitecture() {
  const arch = await getJSON("/api/architecture");
  const col = (title, cls, items) => `
    <div class="arch-col ${cls}">
      <div class="arch-col-head">${title}</div>
      ${items.map(i => `<div class="arch-item"><div class="ai-name">${i.name}</div><div class="ai-detail">${i.detail}</div></div>`).join("")}
    </div>`;
  document.getElementById("archColumns").innerHTML =
    col("Existing standards / components", "real", arch.real) +
    col("Proposed WITNESS firmware", "proposed", arch.proposed) +
    col("Simulated in this prototype", "sim", arch.simulated);

  const diagramSteps = [
    ["AI TRAINING NODES", "checkpoint writes"],
    ["SSD / FTL", "proposed checkpoint state"],
    ["SEALED", "durable, externally queryable"],
    ["NVMe-MI", "real transport"],
    ["BMC / MANAGEMENT PLANE", "real, independent power domain"],
    ["WITNESS COORDINATOR", "in-band first, OOB fallback"],
    ["GLOBAL COMMIT LOG", "O(1) recovery lookup"],
    ["LATEST VALID CHECKPOINT", ""],
  ];
  const el = document.getElementById("archDiagram");
  el.innerHTML = diagramSteps.map(([label, sub], i) => `
    ${i > 0 ? '<div class="diagram-arrow"></div>' : ""}
    <div class="diagram-node ${i === 5 ? "accent" : ""}">${label}${sub ? `<div class="dn-sub">${sub}</div>` : ""}</div>
  `).join("");
}

/* ---------------------------------------------------------------------- */
/* Presentation mode                                                       */
/* ---------------------------------------------------------------------- */

let presentScene = null;

function ensurePresentScene() {
  if (presentScene) return presentScene;
  presentScene = new WitnessScene3D(document.getElementById("scene3dCanvasWrapPresent"), { stepDelayMs: 420 });
  if (!presentScene.available) {
    document.getElementById("scene3dFallbackPresent").style.display = "block";
  }
  return presentScene;
}

function enterPresent() {
  document.body.classList.add("presenting");
  ensurePresentScene();
  presentScene.resize();
  presentScene.start();
}
function exitPresent() {
  document.body.classList.remove("presenting");
  if (presentScene) presentScene.stop();
}

async function runPresentScenarioB() {
  const btn = document.getElementById("btnPresentRun");
  btn.disabled = true;
  btn.textContent = "Running…";
  try {
    const data = await getJSON("/api/run/host_hang");
    renderCompare(document.getElementById("compareGridPresent"), data.runs);
    renderSpeedup(document.getElementById("speedupBannerPresent"), data);
    renderEventLog(document.getElementById("eventLogPresent"), data.runs, { animate: true });
    ensurePresentScene();
    presentScene.loadRun(data);
    if (presentScene.available) {
      presentScene.play();
    } else {
      renderPipeline(document.getElementById("pipelinePresent"), data.runs.witness.events, { animate: true });
    }
  } finally {
    btn.disabled = false;
    btn.textContent = "Run Scenario B";
  }
}

document.getElementById("btnPresent").addEventListener("click", async () => {
  enterPresent();
  await runPresentScenarioB();
});
document.getElementById("btnExitPresent").addEventListener("click", exitPresent);
document.getElementById("btnPresentRun").addEventListener("click", runPresentScenarioB);

/* ---------------------------------------------------------------------- */
/* Boot                                                                     */
/* ---------------------------------------------------------------------- */

(async function boot() {
  try {
    await getJSON("/api/health");
  } catch (e) {
    document.getElementById("apiStatus").textContent = "Simulation: OFFLINE";
    document.getElementById("apiDot").style.background = "#b0281f";
    return;
  }
  await loadOverview();
  await initSimulationPage();
  await initInfrastructurePage();
  await loadArchitecture();
})();

document.addEventListener("visibilitychange", () => {
  if (document.hidden) {
    if (infraScene) infraScene.stop();
    if (presentScene) presentScene.stop();
    return;
  }
  const activeView = document.querySelector(".view.active");
  if (infraScene && activeView && activeView.id === "view-infrastructure") infraScene.start();
  if (presentScene && document.body.classList.contains("presenting")) presentScene.start();
});
