/* WitnessScene3D -- lightweight Three.js visualization of the WITNESS
 * infrastructure pipeline.
 *
 * IMPORTANT: this file computes NO verdicts, latencies, seal states, or
 * node health. It only reads the already-serialized run object returned by
 * the existing backend (dashboard/backend/serialize.py -> run.nodes[],
 * run.events[]) and maps those real values onto simple 3D geometry plus a
 * discrete step timeline driven by the coordinator's own on_event text.
 * Every color a node ends up showing is read from run.nodes[]; the event
 * text only controls WHEN each node's already-known outcome is revealed,
 * so the animation cannot show a state the simulation didn't produce.
 */
(function (global) {
  "use strict";

  const COLORS = {
    pending: 0x9aa3af,
    active: 0x1655c9,
    sealed: 0x1a7f4b,
    failed: 0xb0281f,
    oob: 0xb07a12,
    navy: 0x0b1f3a,
    body: 0xffffff,
    bodyEdge: 0xcdd2db,
    floor: 0xf1f2f5,
  };

  // Per-node event text patterns, matched against the text AFTER a leading
  // "<node_id>: " prefix -- exactly the shape every per-rank message in
  // witness/coordinator.py already uses. Nothing here changes what those
  // messages say; it only classifies them.
  const NODE_EVENT_PATTERNS = [
    [/host process unreachable in-band/, "host_down"],
    [/in-band report -- generation sealed/, "inband_sealed"],
    [/in-band barrier ack received/, "inband_sealed"],
    [/NVMe-MI query[\s\S]*SEALED/, "oob_sealed"],
    [/BMC\/management path unreachable/, "bmc_unreachable"],
    [/fencing window exhausted/, "fenced_out"],
  ];

  function deriveTimeline(events) {
    const steps = [];
    (events || []).forEach((ev) => {
      const msg = ev.message || "";
      const m = msg.match(/^(\S+):\s(.*)$/);
      if (m) {
        const nodeId = m[1], rest = m[2];
        const hit = NODE_EVENT_PATTERNS.find(([re]) => re.test(rest));
        if (hit) steps.push({ kind: hit[1], node_id: nodeId, t_ms: ev.t_ms, message: msg });
        return;
      }
      if (/^checkpoint generation \d+ started/.test(msg)) {
        steps.push({ kind: "start", t_ms: ev.t_ms, message: msg });
      } else if (/confirmed sealed -- committing generation/.test(msg)) {
        steps.push({ kind: "commit", t_ms: ev.t_ms, message: msg });
      } else if (/rejecting generation \d+/.test(msg)) {
        steps.push({ kind: "reject", t_ms: ev.t_ms, message: msg });
      }
    });
    return steps;
  }

  function supportsWebGL() {
    try {
      const c = document.createElement("canvas");
      return !!(global.WebGLRenderingContext &&
        (c.getContext("webgl") || c.getContext("experimental-webgl")));
    } catch (e) {
      return false;
    }
  }

  // Layout: X = pipeline direction (host -> ... -> commit), Z = rank row.
  const X = { host: -9.2, ssd: -4.6, bmc: 0, coord: 4.4, commit: 9.2 };
  const NODE_SPACING = 1.05;

  function zFor(i, n) {
    return (i - (n - 1) / 2) * NODE_SPACING;
  }

  class WitnessScene3D {
    constructor(container, opts) {
      this.container = container;
      this.opts = opts || {};
      this.available = !!(global.THREE) && supportsWebGL();
      this.steps = [];
      this.stepIndex = -1;
      this._lastAppliedIndex = -1;
      this.playing = false;
      this.playTimer = null;
      this.stepDelayMs = this.opts.stepDelayMs || 650;
      this.run = null;
      this.nodeObjects = {};
      this._allNodeIds = [];
      this.finalNodesById = {};
      this._pulses = [];
      this.labels = [];
      this._running = false;
      this._dragging = false;
      if (this.available) this._init3D();
    }

    // ------------------------------------------------------------------
    // Setup
    // ------------------------------------------------------------------

    _init3D() {
      const THREE = global.THREE;
      const el = this.container;
      const w = Math.max(el.clientWidth, 200), h = Math.max(el.clientHeight, 200);

      this.scene = new THREE.Scene();
      this.camera = new THREE.PerspectiveCamera(40, w / h, 0.1, 100);
      this._camAngle = { theta: 0.52, phi: 1.02, radius: 18 };
      this._updateCamera();

      this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
      this.renderer.setPixelRatio(Math.min(global.devicePixelRatio || 1, 2));
      this.renderer.setSize(w, h);
      this.renderer.domElement.style.display = "block";
      el.appendChild(this.renderer.domElement);

      this.scene.add(new THREE.AmbientLight(0xffffff, 0.8));
      const dir = new THREE.DirectionalLight(0xffffff, 0.5);
      dir.position.set(6, 10, 6);
      this.scene.add(dir);
      const dir2 = new THREE.DirectionalLight(0xffffff, 0.22);
      dir2.position.set(-8, 5, -6);
      this.scene.add(dir2);

      const floorGeo = new THREE.PlaneGeometry(26, 11);
      const floorMat = new THREE.MeshStandardMaterial({ color: COLORS.floor, roughness: 1 });
      const floor = new THREE.Mesh(floorGeo, floorMat);
      floor.rotation.x = -Math.PI / 2;
      floor.position.y = -1.05;
      this.scene.add(floor);

      const grid = new THREE.GridHelper(26, 26, 0xdadee5, 0xeceef2);
      grid.position.y = -1.04;
      this.scene.add(grid);

      this.group = new THREE.Group();
      this.scene.add(this.group);
      this.nodeGroup = new THREE.Group();
      this.group.add(this.nodeGroup);

      // Static coordinator + commit-log fixtures.
      this.coordinatorBox = this._makeBox(1.5, 1.7, 1.5);
      this.coordinatorBox.position.set(X.coord, 0.15, 0);
      this.coordinatorBox.userData = { kind: "coordinator" };
      this.group.add(this.coordinatorBox);
      this.coordinatorLed = this._makeLed(0.14);
      this.coordinatorLed.position.set(X.coord, 1.15, 0);
      this.group.add(this.coordinatorLed);

      this.commitBox = this._makeBox(1.35, 1.35, 1.35);
      this.commitBox.position.set(X.commit, 0.075, 0);
      this.commitBox.userData = { kind: "commit" };
      this.group.add(this.commitBox);
      this.commitLed = this._makeLed(0.13);
      this.commitLed.position.set(X.commit, 0.95, 0);
      this.group.add(this.commitLed);

      this._clickables = [this.coordinatorBox, this.commitBox];

      this.raycaster = new THREE.Raycaster();
      this._pointer = new THREE.Vector2();
      this._bindPointer();
      this._bindDrag();

      if (global.ResizeObserver) {
        this._resizeObserver = new ResizeObserver(() => this.resize());
        this._resizeObserver.observe(el);
      }

      this._labelRoot = document.createElement("div");
      this._labelRoot.className = "scene3d-label-layer";
      el.appendChild(this._labelRoot);

      this._lastFrameTime = performance.now();
      this._renderOnce();
    }

    _addLabel(text, pos, cls) {
      const div = document.createElement("div");
      div.className = "scene3d-label " + (cls || "");
      div.textContent = text;
      this._labelRoot.appendChild(div);
      this.labels.push({ el: div, pos });
      return div;
    }

    _makeBox(sx, sy, sz) {
      const THREE = global.THREE;
      const geo = new THREE.BoxGeometry(sx, sy, sz);
      const mat = new THREE.MeshStandardMaterial({ color: COLORS.body, roughness: 0.85, metalness: 0.05 });
      const mesh = new THREE.Mesh(geo, mat);
      const edges = new THREE.LineSegments(
        new THREE.EdgesGeometry(geo),
        new THREE.LineBasicMaterial({ color: COLORS.bodyEdge })
      );
      mesh.add(edges);
      return mesh;
    }

    _makeLed(radius) {
      const THREE = global.THREE;
      const geo = new THREE.SphereGeometry(radius, 16, 12);
      const mat = new THREE.MeshStandardMaterial({
        color: COLORS.pending, emissive: COLORS.pending, emissiveIntensity: 0.55, roughness: 0.4,
      });
      return new THREE.Mesh(geo, mat);
    }

    _setLed(led, hex) {
      led.material.color.setHex(hex);
      led.material.emissive.setHex(hex);
    }

    // ------------------------------------------------------------------
    // Node objects
    // ------------------------------------------------------------------

    _buildNodeObjects(nodeIds) {
      const THREE = global.THREE;
      // dispose previous
      while (this.nodeGroup.children.length) {
        const c = this.nodeGroup.children.pop();
        c.traverse((o) => {
          if (o.geometry) o.geometry.dispose();
          if (o.material) o.material.dispose();
        });
      }
      this.labels = [];
      this._labelRoot.innerHTML = "";
      this._clickables = [this.coordinatorBox, this.commitBox];
      this.nodeObjects = {};

      const n = nodeIds.length;
      nodeIds.forEach((id, i) => {
        const z = zFor(i, n);

        const host = this._makeBox(0.82, 0.56, 0.82);
        host.position.set(X.host, 0.05, z);
        host.userData = { node_id: id };
        const hostLed = this._makeLed(0.1);
        hostLed.position.set(X.host, 0.42, z);

        const ssd = this._makeBox(0.95, 0.22, 0.55);
        ssd.position.set(X.ssd, -0.05, z);
        ssd.userData = { node_id: id };
        const ssdLed = this._makeLed(0.09);
        ssdLed.position.set(X.ssd, 0.15, z);

        const bmc = this._makeBox(0.46, 0.32, 0.46);
        bmc.position.set(X.bmc, 0.0, z);
        bmc.userData = { node_id: id };
        const bmcLed = this._makeLed(0.08);
        bmcLed.position.set(X.bmc, 0.26, z);

        this.nodeGroup.add(host, hostLed, ssd, ssdLed, bmc, bmcLed);
        this._clickables.push(host, ssd, bmc);

        // sideband: SSD -> BMC (always-present wiring)
        const sideLine = this._makeLine([
          new THREE.Vector3(X.ssd, -0.05, z),
          new THREE.Vector3(X.bmc, 0.0, z),
        ], COLORS.pending, false);
        this.nodeGroup.add(sideLine);

        // in-band: host -> coordinator (direct, straight)
        const inbandLine = this._makeLine([
          new THREE.Vector3(X.host, 0.05, z),
          new THREE.Vector3(X.coord, 0.15, 0),
        ], COLORS.pending, false);
        this.nodeGroup.add(inbandLine);

        // out-of-band: BMC -> coordinator, arced up and over so the bypass
        // of the host reads clearly instead of overlapping the in-band line.
        const mid = new THREE.Vector3((X.bmc + X.coord) / 2, 2.1, z * 0.35);
        const curve = new THREE.QuadraticBezierCurve3(
          new THREE.Vector3(X.bmc, 0.16, z),
          mid,
          new THREE.Vector3(X.coord, 0.6, 0)
        );
        const oobPoints = curve.getPoints(24);
        const oobLine = this._makeLine(oobPoints, COLORS.oob, true);
        oobLine.material.opacity = 0.12;
        this.nodeGroup.add(oobLine);

        this._addLabel(id.toUpperCase(), new THREE.Vector3(X.host, -0.35, z), "scene3d-node-label");

        this.nodeObjects[id] = {
          host, hostLed, ssd, ssdLed, bmc, bmcLed, sideLine, inbandLine, oobLine, oobCurve: curve,
          revealed: { host: false, ssd: false, bmc: false },
        };
      });

      this.resize();
    }

    _makeLine(points, colorHex, dashed) {
      const THREE = global.THREE;
      const geo = new THREE.BufferGeometry().setFromPoints(points);
      const mat = dashed
        ? new THREE.LineDashedMaterial({ color: colorHex, dashSize: 0.18, gapSize: 0.12, transparent: true, opacity: 0.9 })
        : new THREE.LineBasicMaterial({ color: colorHex, transparent: true, opacity: 0.35 });
      const line = new THREE.Line(geo, mat);
      if (dashed) line.computeLineDistances();
      return line;
    }

    // ------------------------------------------------------------------
    // Timeline / state application
    // ------------------------------------------------------------------

    loadRun(runData) {
      this.pause();
      const run = (runData && runData.runs && (runData.runs.witness || runData.runs.baseline)) || null;
      this.run = run;
      this.runMeta = runData;
      if (!run) return;
      this.steps = deriveTimeline(run.events);
      this._allNodeIds = run.nodes.map((n) => n.node_id);
      this.finalNodesById = {};
      run.nodes.forEach((n) => { this.finalNodesById[n.node_id] = n; });
      if (this.available) this._buildNodeObjects(this._allNodeIds);
      this._lastAppliedIndex = -1;
      this.setStep(-1);
    }

    getTotalSteps() { return this.steps.length; }
    getStepIndex() { return this.stepIndex; }
    getStepAt(i) { return this.steps[i]; }

    _resetLeds() {
      if (!this.available) return;
      this._setLed(this.coordinatorLed, COLORS.pending);
      this._setLed(this.commitLed, COLORS.pending);
      Object.values(this.nodeObjects).forEach((o) => {
        o.revealed.host = o.revealed.ssd = o.revealed.bmc = false;
        this._setLed(o.hostLed, COLORS.pending);
        this._setLed(o.ssdLed, COLORS.pending);
        this._setLed(o.bmcLed, COLORS.pending);
        o.sideLine.material.color.setHex(COLORS.pending);
        o.sideLine.material.opacity = 0.25;
        o.inbandLine.material.color.setHex(COLORS.pending);
        o.inbandLine.material.opacity = 0.25;
        o.oobLine.material.color.setHex(COLORS.oob);
        o.oobLine.material.opacity = 0.12;
      });
    }

    setStep(index) {
      index = Math.max(-1, Math.min(index, this.steps.length - 1));
      const prev = this._lastAppliedIndex;
      this._resetLeds();
      for (let i = 0; i <= index; i++) {
        this._applyStepForward(this.steps[i], i <= prev);
      }
      if (index === this.steps.length - 1 && index >= 0 && this.available) {
        this._allNodeIds.forEach((id) => this._reveal(id, ["host", "ssd", "bmc"]));
      }
      this._lastAppliedIndex = index;
      this.stepIndex = index;
      if (this.opts.onStep) {
        this.opts.onStep(index >= 0 ? this.steps[index] : null, index, this.steps.length);
      }
      this._requestRender();
    }

    _reveal(nodeId, parts) {
      if (!this.available) return;
      const nd = this.finalNodesById[nodeId];
      const obj = this.nodeObjects[nodeId];
      if (!nd || !obj) return;
      if (parts.includes("host") && !obj.revealed.host) {
        obj.revealed.host = true;
        this._setLed(obj.hostLed, nd.host_alive ? COLORS.sealed : COLORS.failed);
        obj.inbandLine.material.color.setHex(nd.host_alive ? COLORS.active : COLORS.failed);
        obj.inbandLine.material.opacity = nd.host_alive ? 0.75 : 0.3;
      }
      if (parts.includes("ssd") && !obj.revealed.ssd) {
        obj.revealed.ssd = true;
        let c = COLORS.pending;
        if (!nd.ssd_powered) c = COLORS.failed;
        else if (nd.shard_status === "SEALED") c = COLORS.sealed;
        else c = COLORS.active;
        this._setLed(obj.ssdLed, c);
      }
      if (parts.includes("bmc") && !obj.revealed.bmc) {
        obj.revealed.bmc = true;
        this._setLed(obj.bmcLed, nd.bmc_reachable ? COLORS.sealed : COLORS.failed);
        obj.oobLine.material.opacity = nd.bmc_reachable ? 0.85 : 0.55;
        obj.oobLine.material.color.setHex(nd.bmc_reachable ? COLORS.oob : COLORS.failed);
        obj.oobLine.computeLineDistances();
        obj.sideLine.material.color.setHex(nd.bmc_reachable ? COLORS.oob : COLORS.failed);
        obj.sideLine.material.opacity = 0.7;
      }
    }

    _applyStepForward(step, silent) {
      const obj = step.node_id ? this.nodeObjects[step.node_id] : null;
      switch (step.kind) {
        case "start":
          this._allNodeIds.forEach((id) => {
            const o = this.nodeObjects[id];
            if (!o) return;
            this._setLed(o.hostLed, COLORS.sealed);
            this._setLed(o.ssdLed, COLORS.active);
            o.inbandLine.material.opacity = 0.4;
          });
          this._setLed(this.coordinatorLed, COLORS.active);
          break;
        case "host_down":
          this._reveal(step.node_id, ["host"]);
          if (!silent && obj) this._pulseLine(obj.sideLine, COLORS.oob);
          break;
        case "inband_sealed":
          this._reveal(step.node_id, ["ssd"]);
          if (!silent && obj) this._pulseSegment(new global.THREE.Vector3(X.host, 0.05, obj.host.position.z), new global.THREE.Vector3(X.coord, 0.15, 0), COLORS.active);
          break;
        case "oob_sealed":
          this._reveal(step.node_id, ["ssd", "bmc"]);
          if (!silent && obj) this._pulseCurve(obj.oobCurve, COLORS.oob);
          break;
        case "bmc_unreachable":
          this._reveal(step.node_id, ["bmc"]);
          break;
        case "fenced_out":
          this._reveal(step.node_id, ["host", "ssd", "bmc"]);
          break;
        case "commit":
          this._setLed(this.coordinatorLed, COLORS.sealed);
          this._setLed(this.commitLed, COLORS.sealed);
          break;
        case "reject":
          this._setLed(this.coordinatorLed, COLORS.failed);
          this._setLed(this.commitLed, COLORS.failed);
          break;
      }
    }

    // ------------------------------------------------------------------
    // Playback controls
    // ------------------------------------------------------------------

    play() {
      if (this.playing) return;
      if (this.stepIndex >= this.steps.length - 1) this.setStep(-1);
      this.playing = true;
      const tick = () => {
        if (!this.playing) return;
        if (this.stepIndex >= this.steps.length - 1) {
          this.playing = false;
          if (this.opts.onPlayState) this.opts.onPlayState(false, true);
          return;
        }
        this.setStep(this.stepIndex + 1);
        this.playTimer = setTimeout(tick, this.stepDelayMs);
      };
      tick();
      if (this.opts.onPlayState) this.opts.onPlayState(true, false);
    }

    pause() {
      this.playing = false;
      if (this.playTimer) { clearTimeout(this.playTimer); this.playTimer = null; }
      if (this.opts.onPlayState) this.opts.onPlayState(false, false);
    }

    reset() {
      this.pause();
      this.setStep(-1);
    }

    // ------------------------------------------------------------------
    // Pulses (transient traveling markers along a path)
    // ------------------------------------------------------------------

    _pulseSegment(from, to, colorHex) {
      if (!this.available) return;
      const THREE = global.THREE;
      const mesh = new THREE.Mesh(
        new THREE.SphereGeometry(0.11, 12, 10),
        new THREE.MeshStandardMaterial({ color: colorHex, emissive: colorHex, emissiveIntensity: 0.8 })
      );
      this.group.add(mesh);
      this._pulses.push({ mesh, from, to, t0: performance.now(), duration: 620 });
    }

    _pulseLine(line, colorHex) {
      const pos = line.geometry.attributes.position;
      const from = new global.THREE.Vector3(pos.getX(0), pos.getY(0), pos.getZ(0));
      const last = pos.count - 1;
      const to = new global.THREE.Vector3(pos.getX(last), pos.getY(last), pos.getZ(last));
      this._pulseSegment(from, to, colorHex);
    }

    _pulseCurve(curve, colorHex) {
      if (!this.available) return;
      const THREE = global.THREE;
      const mesh = new THREE.Mesh(
        new THREE.SphereGeometry(0.12, 12, 10),
        new THREE.MeshStandardMaterial({ color: colorHex, emissive: colorHex, emissiveIntensity: 0.85 })
      );
      this.group.add(mesh);
      this._pulses.push({ mesh, curve, t0: performance.now(), duration: 780 });
    }

    _updatePulses(now) {
      if (!this._pulses.length) return;
      this._pulses = this._pulses.filter((p) => {
        const t = Math.min(1, (now - p.t0) / p.duration);
        if (p.curve) {
          const pt = p.curve.getPoint(t);
          p.mesh.position.copy(pt);
        } else {
          p.mesh.position.lerpVectors(p.from, p.to, t);
        }
        p.mesh.scale.setScalar(1 - Math.abs(t - 0.5) * 0.4);
        if (t >= 1) {
          this.group.remove(p.mesh);
          p.mesh.geometry.dispose();
          p.mesh.material.dispose();
          return false;
        }
        return true;
      });
    }

    // ------------------------------------------------------------------
    // Camera / interaction
    // ------------------------------------------------------------------

    _updateCamera() {
      const { theta, phi, radius } = this._camAngle;
      const x = radius * Math.sin(phi) * Math.sin(theta);
      const y = radius * Math.cos(phi);
      const z = radius * Math.sin(phi) * Math.cos(theta);
      this.camera.position.set(x, y, z);
      this.camera.lookAt(0, -0.1, 0);
    }

    _bindDrag() {
      const el = this.renderer.domElement;
      let lastX = 0, lastY = 0;
      const down = (e) => {
        this._dragging = true;
        lastX = e.clientX; lastY = e.clientY;
      };
      const move = (e) => {
        if (!this._dragging) return;
        const dx = e.clientX - lastX, dy = e.clientY - lastY;
        lastX = e.clientX; lastY = e.clientY;
        this._camAngle.theta -= dx * 0.006;
        this._camAngle.phi = Math.max(0.5, Math.min(1.45, this._camAngle.phi - dy * 0.005));
        this._updateCamera();
        this._requestRender();
      };
      const up = () => { this._dragging = false; };
      el.addEventListener("pointerdown", down);
      global.addEventListener("pointermove", move);
      global.addEventListener("pointerup", up);
      el.addEventListener("wheel", (e) => {
        e.preventDefault();
        this._camAngle.radius = Math.max(9, Math.min(24, this._camAngle.radius + e.deltaY * 0.01));
        this._updateCamera();
        this._requestRender();
      }, { passive: false });
    }

    _bindPointer() {
      const el = this.renderer.domElement;
      let downX = 0, downY = 0;
      el.addEventListener("pointerdown", (e) => { downX = e.clientX; downY = e.clientY; });
      el.addEventListener("pointerup", (e) => {
        if (Math.abs(e.clientX - downX) > 4 || Math.abs(e.clientY - downY) > 4) return; // was a drag
        const rect = el.getBoundingClientRect();
        this._pointer.x = ((e.clientX - rect.left) / rect.width) * 2 - 1;
        this._pointer.y = -((e.clientY - rect.top) / rect.height) * 2 + 1;
        this.raycaster.setFromCamera(this._pointer, this.camera);
        const hits = this.raycaster.intersectObjects(this._clickables, false);
        if (hits.length && this.opts.onNodeClick) {
          const ud = hits[0].object.userData || {};
          if (ud.node_id) {
            this.opts.onNodeClick(this.finalNodesById[ud.node_id], ud.node_id, null);
          } else if (ud.kind) {
            this.opts.onNodeClick(null, null, { kind: ud.kind, run: this.run });
          }
        }
      });
    }

    // ------------------------------------------------------------------
    // Render loop
    // ------------------------------------------------------------------

    _updateLabels() {
      if (!this.labels.length) return;
      const el = this.container;
      const w = el.clientWidth, h = el.clientHeight;
      const v = new global.THREE.Vector3();
      this.labels.forEach((l) => {
        v.copy(l.pos).project(this.camera);
        const x = (v.x * 0.5 + 0.5) * w;
        const y = (-v.y * 0.5 + 0.5) * h;
        l.el.style.transform = `translate(${x}px, ${y}px) translate(-50%, -50%)`;
        l.el.style.display = v.z > 1 ? "none" : "block";
      });
    }

    _requestRender() {
      if (!this.available) return;
      if (this._running) return; // loop already renders every frame
      this._renderOnce();
    }

    _renderOnce() {
      if (!this.available) return;
      this._updatePulses(performance.now());
      this._updateLabels();
      this.renderer.render(this.scene, this.camera);
    }

    start() {
      if (!this.available || this._running) return;
      this._running = true;
      const loop = () => {
        if (!this._running) return;
        if (!this._dragging) {
          this._camAngle.theta += 0.0018;
          this._updateCamera();
        }
        this._renderOnce();
        this._raf = global.requestAnimationFrame(loop);
      };
      this._raf = global.requestAnimationFrame(loop);
    }

    stop() {
      this._running = false;
      if (this._raf) global.cancelAnimationFrame(this._raf);
      this._raf = null;
    }

    resize() {
      if (!this.available) return;
      const el = this.container;
      const w = Math.max(el.clientWidth, 200), h = Math.max(el.clientHeight, 200);
      this.camera.aspect = w / h;
      this.camera.updateProjectionMatrix();
      this.renderer.setSize(w, h);
      this._requestRender();
    }

    dispose() {
      this.pause();
      this.stop();
      if (this._resizeObserver) this._resizeObserver.disconnect();
      if (this.available && this.renderer) {
        this.renderer.dispose();
        if (this.renderer.domElement.parentNode) this.renderer.domElement.parentNode.removeChild(this.renderer.domElement);
      }
      if (this._labelRoot && this._labelRoot.parentNode) this._labelRoot.parentNode.removeChild(this._labelRoot);
    }
  }

  global.WitnessScene3D = WitnessScene3D;
  global.WitnessScene3D.supportsWebGL = supportsWebGL;
  global.WitnessScene3D._deriveTimeline = deriveTimeline; // exposed for testing/debugging only
})(window);
