"use strict";

(function attachAirportSurface(global) {
  const COLORS = Object.freeze({
    cyan: "#45d8ff",
    blue: "#6c8dff",
    green: "#4fe0a3",
    amber: "#ffc45e",
    red: "#ff6f86",
    ink: "#ecf8fa",
  });

  const AIRPORT_PROFILES = Object.freeze({
    ORD: {
      shortName: "O’Hare", terminal: "O’HARE TERMINAL COMPLEX", weather: "snow",
      runwayYs: [0.18, 0.43, 0.68], gateXs: [0.29, 0.42, 0.57, 0.71],
      accent: "#45d8ff",
    },
    ATL: {
      shortName: "Atlanta", terminal: "ATL MIDFIELD CONCOURSES", weather: "storm",
      runwayYs: [0.18, 0.43, 0.68], gateXs: [0.27, 0.40, 0.58, 0.72],
      accent: "#ffc45e",
    },
    SFO: {
      shortName: "San Francisco", terminal: "SFO TERMINAL COMPLEX", weather: "rain",
      runwayYs: [0.20, 0.45, 0.70], gateXs: [0.28, 0.41, 0.59, 0.73],
      accent: "#8bdff3",
    },
  });

  const FALLBACK_RUNWAYS = [
    { id: "RWY-1", y: 0.18, label: "RUNWAY 1" },
    { id: "RWY-2", y: 0.43, label: "RUNWAY 2" },
    { id: "RWY-3", y: 0.68, label: "RUNWAY 3" },
  ];

  const FALLBACK_GATES = [
    { id: "G1", x: 0.29, y: 0.88 }, { id: "G2", x: 0.42, y: 0.86 },
    { id: "G3", x: 0.57, y: 0.86 }, { id: "G4", x: 0.71, y: 0.88 },
  ];

  const clamp = (value, min = 0, max = 1) => Math.max(min, Math.min(max, value));
  const ease = (value) => { const t = clamp(value); return t * t * (3 - 2 * t); };
  const mix = (a, b, t) => a + (b - a) * t;

  function roundRect(context, x, y, width, height, radius) {
    const r = Math.min(Math.abs(width) / 2, Math.abs(height) / 2, Math.max(0, radius));
    context.beginPath();
    context.moveTo(x + r, y);
    context.arcTo(x + width, y, x + width, y + height, r);
    context.arcTo(x + width, y + height, x, y + height, r);
    context.arcTo(x, y + height, x, y, r);
    context.arcTo(x, y, x + width, y, r);
    context.closePath();
  }

  function pathPoint(points, progress) {
    if (!points.length) return { x: 0, y: 0, angle: 0 };
    if (points.length === 1) return { ...points[0], angle: 0 };
    const lengths = [];
    let total = 0;
    for (let index = 1; index < points.length; index += 1) {
      const length = Math.hypot(points[index].x - points[index - 1].x, points[index].y - points[index - 1].y);
      lengths.push(length); total += length;
    }
    let distance = clamp(progress) * total;
    for (let index = 0; index < lengths.length; index += 1) {
      if (distance <= lengths[index] || index === lengths.length - 1) {
        const ratio = lengths[index] ? distance / lengths[index] : 0;
        const a = points[index]; const b = points[index + 1];
        return { x: mix(a.x, b.x, ratio), y: mix(a.y, b.y, ratio), angle: Math.atan2(b.y - a.y, b.x - a.x) };
      }
      distance -= lengths[index];
    }
    return { ...points.at(-1), angle: 0 };
  }

  function gateFor(flight, index, gates) {
    const requested = String(flight.gate || "").toUpperCase();
    return gates.find((gate) => gate.id === requested) || gates[index % gates.length];
  }

  class AirportSurface {
    constructor(canvas, options = {}) {
      this.canvas = typeof canvas === "string" ? document.querySelector(canvas) : canvas;
      if (!this.canvas) throw new Error("Airport canvas was not found");
      this.context = this.canvas.getContext("2d", { alpha: false });
      this.snapshot = null;
      this.airportCode = "ORD";
      this.profile = AIRPORT_PROFILES.ORD;
      this.runways = FALLBACK_RUNWAYS;
      this.gates = FALLBACK_GATES;
      this.engineAvailable = false;
      this.time = 0;
      this.planApplied = false;
      this.selectedFlightId = null;
      this.animation = 0;
      this.destroyed = false;
      this.snow = Array.from({ length: 58 }, (_, index) => ({
        x: ((index * 47) % 101) / 100,
        y: ((index * 73) % 97) / 96,
        r: 0.6 + (index % 4) * 0.32,
        speed: 0.00003 + (index % 6) * 0.000007,
      }));
      this.resizeObserver = new ResizeObserver(() => this.resize());
      this.resizeObserver.observe(this.canvas);
      this.resize();
      this.loop = this.loop.bind(this);
      this.animation = requestAnimationFrame(this.loop);
      this.onCounts = options.onCounts || (() => {});
    }

    setSnapshot(snapshot) {
      this.snapshot = snapshot || null;
      this.airportCode = String(snapshot?.scenario?.airport || "ORD").toUpperCase();
      this.profile = AIRPORT_PROFILES[this.airportCode] || AIRPORT_PROFILES.ORD;
      const resources = Array.isArray(snapshot?.resources) ? snapshot.resources : [];
      const runwayResources = resources.filter((item) => String(item?.type).toUpperCase() === "RUNWAY");
      const gateResources = resources.filter((item) => String(item?.type).toUpperCase() === "GATE");
      this.runways = runwayResources.length ? runwayResources.map((item, index) => ({
        ...item,
        y: this.profile.runwayYs[index] ?? (0.18 + index * (0.5 / Math.max(1, runwayResources.length - 1))),
        label: String(item.label || item.id || `RUNWAY ${index + 1}`).replace(" / ", " · "),
      })) : FALLBACK_RUNWAYS;
      this.gates = gateResources.length ? gateResources.map((item, index) => ({
        ...item,
        x: this.profile.gateXs[index] ?? (0.28 + index * (0.45 / Math.max(1, gateResources.length - 1))),
        y: index === 0 || index === gateResources.length - 1 ? 0.88 : 0.86,
      })) : FALLBACK_GATES;
      const plan = snapshot?.current_plan;
      const historical = Boolean(plan?.display_only || plan?.historical || plan?.is_current === false);
      this.planApplied = !historical && ["APPLIED", "EXECUTING", "COMPLETED"].includes(String(plan?.status || "").toUpperCase());
    }

    setTime(minute) { this.time = Number.isFinite(Number(minute)) ? Number(minute) : 0; }
    setEngineAvailable(active) { this.engineAvailable = Boolean(active); }
    setSelectedFlight(id) { this.selectedFlightId = id || null; }
    setPlanApplied(active) { this.planApplied = Boolean(active); }

    resize() {
      const rect = this.canvas.getBoundingClientRect();
      const scale = Math.min(global.devicePixelRatio || 1, 2);
      const width = Math.max(320, Math.round(rect.width * scale));
      const height = Math.max(240, Math.round(rect.height * scale));
      if (this.canvas.width !== width || this.canvas.height !== height) {
        this.canvas.width = width; this.canvas.height = height;
      }
    }

    loop(timestamp) {
      if (this.destroyed) return;
      this.draw(timestamp || performance.now());
      this.animation = requestAnimationFrame(this.loop);
    }

    destroy() {
      this.destroyed = true;
      cancelAnimationFrame(this.animation);
      this.resizeObserver.disconnect();
    }

    aircraftStates() {
      const flights = Array.isArray(this.snapshot?.flights) ? this.snapshot.flights : [];
      const currentPlan = this.planApplied ? this.snapshot?.current_plan : null;
      const planTargets = new Set((currentPlan?.actions || []).map((action) => action?.target_id).filter(Boolean));
      const planFlightPath = new Set((currentPlan?.causal_path?.nodes || [])
        .filter((node) => String(node?.type || "").toUpperCase().includes("FLIGHT"))
        .map((node) => node?.id)
        .filter(Boolean));
      const states = flights.map((flight, index) => {
        const operation = String(flight.kind || flight.direction || flight.operation || "departure").toLowerCase();
        const kind = operation.includes("arr") || operation.includes("inbound") || operation === "in" ? "arrival" : "departure";
        const minute = Number(flight.minute ?? flight.scheduled_minute ?? flight.event_minute ?? 0);
        const gate = gateFor(flight, index, this.gates);
        const requestedRunway = String(flight.runway || "").toUpperCase();
        const runway = this.runways.find((item) => String(item.id).toUpperCase() === requestedRunway)
          || this.runways[index % this.runways.length];
        const risk = Boolean(flight.at_risk || flight.risk || ["DELAYED", "AT_RISK", "CONFLICT"].includes(String(flight.status || "").toUpperCase()));
        const protectedFlight = Boolean(flight.protected || this.planApplied && (
          flight.plan_target || risk || planTargets.has(flight.id) || planFlightPath.has(flight.id)
        ));
        const label = flight.label || flight.callsign || flight.flight_number || flight.id || `FLIGHT ${index + 1}`;
        const state = { ...flight, id: flight.id || label, label, kind, minute, gate, runway, risk, protected: protectedFlight };
        if (kind === "arrival") {
          const approachStart = minute - 18;
          if (this.time < approachStart || this.time > minute + 35) return null;
          if (this.time < minute + 2) {
            const p = ease((this.time - approachStart) / 20);
            state.phase = this.time < minute - 3 ? "airborne" : "landing";
            state.position = pathPoint([{ x: -0.08, y: runway.y }, { x: 0.27, y: runway.y }], p);
          } else if (this.time < minute + 10) {
            state.phase = "taxi";
            state.position = pathPoint([{ x: 0.27, y: runway.y }, { x: 0.25, y: 0.79 }, { x: gate.x, y: 0.79 }, gate], ease((this.time - minute - 2) / 8));
          } else { state.phase = "gate"; state.position = { ...gate, angle: -Math.PI / 2 }; }
        } else {
          const pushStart = minute - 14;
          if (this.time < pushStart - 24 || this.time > minute + 10) return null;
          if (this.time < pushStart) { state.phase = "gate"; state.position = { ...gate, angle: -Math.PI / 2 }; }
          else if (this.time < minute) {
            state.phase = "taxi";
            state.position = pathPoint([gate, { x: gate.x, y: 0.79 }, { x: 0.76, y: 0.79 }, { x: 0.76, y: runway.y }], ease((this.time - pushStart) / 14));
          } else {
            state.phase = "takeoff";
            state.position = pathPoint([{ x: 0.76, y: runway.y }, { x: 1.08, y: runway.y }], ease((this.time - minute) / 10));
          }
        }
        return state;
      }).filter(Boolean);

      // An inbound and its following outbound describe one physical aircraft.
      // During the short overlap at the gate, keep the outbound label so the
      // surface view never draws the same airframe twice.
      const byRotation = new Map();
      states.forEach((state) => {
        const key = state.rotation_id || state.id;
        const current = byRotation.get(key);
        if (!current || (current.phase === "gate" && state.phase === "gate" && state.kind === "departure")) {
          byRotation.set(key, state);
        }
      });
      return [...byRotation.values()];
    }

    draw(timestamp) {
      const context = this.context; const width = this.canvas.width; const height = this.canvas.height;
      const operational = this.engineAvailable && this.snapshot?.ok === true;
      context.clearRect(0, 0, width, height);
      this.drawGround(context, width, height);
      this.drawRunways(context, width, height);
      this.drawTaxiways(context, width, height);
      this.drawTerminal(context, width, height);
      if (operational) this.drawCapacity(context, width, height, timestamp);
      const aircraft = this.aircraftStates();
      aircraft.forEach((item) => this.drawFlight(context, width, height, item));
      if (operational) { this.drawVehicles(context, width, height, timestamp); this.drawWeather(context, width, height, timestamp); }
      const counts = {
        airborne: aircraft.filter((item) => ["airborne", "landing", "takeoff"].includes(item.phase)).length,
        taxiing: aircraft.filter((item) => item.phase === "taxi").length,
        atGate: aircraft.filter((item) => item.phase === "gate").length,
        risk: aircraft.filter((item) => item.risk && !item.protected).length,
        protected: aircraft.filter((item) => item.protected).length,
      };
      const signature = `${operational}:${Object.values(counts).join(":")}`;
      if (signature !== this.countSignature) {
        this.countSignature = signature; this.onCounts(counts);
        this.canvas.setAttribute("aria-label", operational
          ? `Stylized ${this.profile.shortName} operations replay. ${counts.airborne} airborne, ${counts.taxiing} taxiing, ${counts.atGate} at a gate, ${counts.risk} at risk and ${counts.protected} protected by the selected replay decision.`
          : this.snapshot?.ok === true ? "Airport replay paused on the last genuine server snapshot." : "Airport replay paused. FalkorDB-backed engine state is required to animate operations.");
      }
    }

    drawGround(context, width, height) {
      const gradient = context.createLinearGradient(0, 0, width, height);
      gradient.addColorStop(0, "#0b2832"); gradient.addColorStop(0.55, "#0c3038"); gradient.addColorStop(1, "#071c27");
      context.fillStyle = gradient; context.fillRect(0, 0, width, height);
      const glow = context.createRadialGradient(width * 0.54, height * 0.72, 0, width * 0.54, height * 0.72, width * 0.68);
      glow.addColorStop(0, "rgba(69,216,255,.075)"); glow.addColorStop(1, "rgba(4,17,24,0)");
      context.fillStyle = glow; context.fillRect(0, 0, width, height);
      context.strokeStyle = "rgba(129,178,190,.035)"; context.lineWidth = 1;
      for (let x = 0; x < width; x += width / 18) { context.beginPath(); context.moveTo(x, 0); context.lineTo(x, height); context.stroke(); }
      for (let y = 0; y < height; y += height / 12) { context.beginPath(); context.moveTo(0, y); context.lineTo(width, y); context.stroke(); }
    }

    drawRunways(context, width, height) {
      const unit = Math.max(1, width / 1100);
      this.runways.forEach((runway, index) => {
        const y = runway.y * height; const h = Math.max(16 * unit, height * 0.044);
        context.fillStyle = index % 2 ? "rgba(37,55,64,.96)" : "rgba(41,60,69,.96)";
        roundRect(context, width * 0.035, y - h / 2, width * 0.93, h, h * 0.12); context.fill();
        context.strokeStyle = "rgba(244,250,250,.65)"; context.lineWidth = 1.05 * unit; context.setLineDash([14 * unit, 12 * unit]);
        context.beginPath(); context.moveTo(width * 0.075, y); context.lineTo(width * 0.925, y); context.stroke(); context.setLineDash([]);
        context.fillStyle = "rgba(211,230,234,.67)"; context.font = `700 ${Math.max(6, width * 0.0052)}px ui-monospace,monospace`; context.textAlign = "left"; context.textBaseline = "bottom";
        context.fillText(runway.label, width * 0.045, y - h * 0.64);
        for (let mark = 0; mark < 5; mark += 1) {
          context.fillStyle = "rgba(242,248,248,.72)";
          context.fillRect(width * (0.055 + mark * 0.012), y - h * 0.25, width * 0.006, h * 0.5);
          context.fillRect(width * (0.93 - mark * 0.012), y - h * 0.25, width * 0.006, h * 0.5);
        }
      });
    }

    drawTaxiways(context, width, height) {
      const unit = Math.max(1, width / 1100);
      const paths = [
        [[0.18, 0.79], [0.83, 0.79]], [[0.25, 0.13], [0.25, 0.79]], [[0.76, 0.13], [0.76, 0.79]],
        ...this.gates.map((gate) => [[gate.x, 0.79], [gate.x, gate.y]]),
      ];
      context.lineCap = "round"; context.lineJoin = "round";
      paths.forEach((points) => {
        context.strokeStyle = "rgba(86,105,112,.95)"; context.lineWidth = 17 * unit;
        context.beginPath(); points.forEach(([x, y], index) => index ? context.lineTo(x * width, y * height) : context.moveTo(x * width, y * height)); context.stroke();
        context.strokeStyle = "rgba(248,206,72,.78)"; context.lineWidth = 1.05 * unit;
        context.beginPath(); points.forEach(([x, y], index) => index ? context.lineTo(x * width, y * height) : context.moveTo(x * width, y * height)); context.stroke();
      });
      context.lineCap = "butt";
      for (let x = 0.19; x <= 0.82; x += 0.026) {
        context.fillStyle = "rgba(80,188,245,.72)"; context.shadowColor = "rgba(69,216,255,.55)"; context.shadowBlur = 4 * unit;
        context.beginPath(); context.arc(x * width, 0.765 * height, Math.max(0.9, unit), 0, Math.PI * 2); context.fill();
      }
      context.shadowColor = "transparent";
    }

    drawTerminal(context, width, height) {
      const unit = Math.max(1, width / 1100);
      context.shadowColor = "rgba(0,0,0,.35)"; context.shadowBlur = 18 * unit; context.fillStyle = "#d7e3e3";
      roundRect(context, width * 0.18, height * 0.84, width * 0.64, height * 0.13, height * 0.02); context.fill();
      [0.31, 0.49, 0.68].forEach((x) => { roundRect(context, width * (x - 0.035), height * 0.78, width * 0.07, height * 0.12, height * 0.016); context.fill(); });
      context.shadowColor = "transparent"; context.fillStyle = "#9eb1b4";
      roundRect(context, width * 0.205, height * 0.88, width * 0.59, height * 0.055, height * 0.009); context.fill();
      context.fillStyle = "rgba(253,213,95,.86)";
      for (let x = 0.22; x < 0.79; x += 0.024) { roundRect(context, x * width, height * 0.895, width * 0.012, height * 0.018, 2 * unit); context.fill(); }
      context.fillStyle = "rgba(13,39,47,.68)"; context.font = `850 ${Math.max(7, width * 0.006)}px Inter,system-ui`; context.textAlign = "left"; context.textBaseline = "middle";
      context.fillText(this.profile.terminal, width * 0.215, height * 0.858);
      this.gates.forEach((gate) => {
        context.strokeStyle = "rgba(247,252,252,.42)"; context.lineWidth = unit; context.setLineDash([4 * unit, 4 * unit]);
        context.beginPath(); context.arc(gate.x * width, gate.y * height, width * 0.024, Math.PI * 1.08, Math.PI * 1.92); context.stroke(); context.setLineDash([]);
        context.fillStyle = "rgba(13,36,43,.85)"; roundRect(context, gate.x * width - 13 * unit, gate.y * height + 9 * unit, 26 * unit, 12 * unit, 4 * unit); context.fill();
        context.fillStyle = "rgba(236,247,248,.85)"; context.font = `800 ${Math.max(5.5, width * 0.005)}px ui-monospace,monospace`; context.textAlign = "center"; context.fillText(gate.id, gate.x * width, gate.y * height + 15 * unit);
      });
    }

    drawCapacity(context, width, height, timestamp) {
      const cycleStatus = String(this.snapshot?.replanning?.status || "").toUpperCase();
      const running = cycleStatus.includes("RUN") || cycleStatus.includes("ANALYZ");
      const activeEvent = (Array.isArray(this.snapshot?.events) ? this.snapshot.events : [])
        .find((event) => String(event?.status).toUpperCase() === "ACTIVE");
      if (!running && !activeEvent) return;
      const pulse = 0.45 + Math.sin(timestamp / 420) * 0.16;
      context.save(); context.globalAlpha = running ? pulse : 0.32;
      context.fillStyle = running ? "rgba(69,216,255,.25)" : "rgba(255,196,94,.18)";
      roundRect(context, width * 0.405, height * 0.335, width * 0.19, height * 0.07, height * 0.012); context.fill();
      context.fillStyle = running ? "#b6effc" : "#f7d594"; context.font = `800 ${Math.max(6, width * 0.0052)}px Inter,system-ui`; context.textAlign = "center"; context.textBaseline = "middle";
      const eventLabel = String(activeEvent?.type || "ACTIVE OPERATIONAL CHANGE").replaceAll("_", " ");
      context.fillText(running ? "GRAPH REPLAN IN PROGRESS" : eventLabel, width * 0.5, height * 0.37); context.restore();
    }

    drawFlight(context, width, height, item) {
      const unit = Math.max(0.72, width / 1100); const x = item.position.x * width; const y = item.position.y * height; const selected = item.id === this.selectedFlightId;
      const color = item.protected ? COLORS.green : item.risk ? COLORS.amber : ["airborne", "takeoff", "landing"].includes(item.phase) ? COLORS.cyan : COLORS.blue;
      if (["airborne", "takeoff", "landing", "taxi"].includes(item.phase)) {
        const trailLength = ["airborne", "takeoff", "landing"].includes(item.phase) ? width * 0.055 : width * 0.025;
        const gradient = context.createLinearGradient(x, y, x - Math.cos(item.position.angle) * trailLength, y - Math.sin(item.position.angle) * trailLength);
        gradient.addColorStop(0, color); gradient.addColorStop(1, "rgba(255,255,255,0)");
        context.strokeStyle = gradient; context.lineWidth = 2.2 * unit; context.lineCap = "round"; context.beginPath(); context.moveTo(x, y); context.lineTo(x - Math.cos(item.position.angle) * trailLength, y - Math.sin(item.position.angle) * trailLength); context.stroke();
      }
      if (selected || item.protected) {
        context.strokeStyle = color; context.lineWidth = selected ? 2.3 * unit : 1.4 * unit; context.setLineDash([4 * unit, 4 * unit]); context.lineDashOffset = -this.time * unit;
        context.beginPath(); context.arc(x, y, width * (selected ? 0.027 : 0.022), 0, Math.PI * 2); context.stroke(); context.setLineDash([]);
      }
      context.save(); context.translate(x, y); context.rotate(item.position.angle || 0); context.shadowColor = color; context.shadowBlur = 10 * unit; context.fillStyle = COLORS.ink;
      const scale = unit * (["airborne", "takeoff", "landing"].includes(item.phase) ? 1.05 : 0.86);
      context.beginPath(); context.moveTo(13 * scale, 0); context.lineTo(2 * scale, -2.5 * scale); context.lineTo(-4 * scale, -9 * scale); context.lineTo(-7 * scale, -8 * scale); context.lineTo(-4 * scale, -1.7 * scale); context.lineTo(-12 * scale, -1.2 * scale); context.lineTo(-14 * scale, -4 * scale); context.lineTo(-16 * scale, -3 * scale); context.lineTo(-14 * scale, 0); context.lineTo(-16 * scale, 3 * scale); context.lineTo(-14 * scale, 4 * scale); context.lineTo(-12 * scale, 1.2 * scale); context.lineTo(-4 * scale, 1.7 * scale); context.lineTo(-7 * scale, 8 * scale); context.lineTo(-4 * scale, 9 * scale); context.lineTo(2 * scale, 2.5 * scale); context.closePath(); context.fill();
      context.shadowColor = "transparent"; context.fillStyle = color; context.beginPath(); context.arc(8 * scale, 0, 1.8 * scale, 0, Math.PI * 2); context.fill(); context.restore();
      context.save(); context.font = `850 ${Math.max(6.5, width * 0.006)}px ui-monospace,monospace`; const text = String(item.label); const textWidth = context.measureText(text).width;
      context.fillStyle = "rgba(3,15,22,.88)"; roundRect(context, x - textWidth / 2 - 5 * unit, y - 21 * unit, textWidth + 10 * unit, 13 * unit, 4 * unit); context.fill();
      context.fillStyle = color; context.textAlign = "center"; context.textBaseline = "middle"; context.fillText(text, x, y - 14.5 * unit); context.restore();
    }

    drawVehicles(context, width, height, timestamp) {
      const unit = Math.max(0.75, width / 1100);
      [{ offset: 0.1, y: 0.81, color: COLORS.cyan }, { offset: 0.55, y: 0.82, color: COLORS.amber }].forEach((vehicle, index) => {
        const x = (0.2 + ((timestamp * 0.000025 + vehicle.offset) % 0.57)) * width; const y = vehicle.y * height + index * 4 * unit;
        context.shadowColor = vehicle.color; context.shadowBlur = 5 * unit; context.fillStyle = vehicle.color; roundRect(context, x - 6 * unit, y - 3 * unit, 12 * unit, 6 * unit, 2 * unit); context.fill(); context.shadowColor = "transparent";
      });
    }

    drawWeather(context, width, height, timestamp) {
      if (this.profile.weather === "snow") {
        context.fillStyle = "rgba(229,247,250,.42)";
        this.snow.forEach((flake, index) => {
          const y = (flake.y + timestamp * flake.speed) % 1; const x = (flake.x + Math.sin(timestamp / 1100 + index) * 0.008 + 1) % 1;
          context.beginPath(); context.arc(x * width, y * height, flake.r * Math.max(0.65, width / 1200), 0, Math.PI * 2); context.fill();
        });
        return;
      }
      context.save();
      context.strokeStyle = this.profile.weather === "storm" ? "rgba(199,226,238,.25)" : "rgba(189,229,243,.20)";
      context.lineWidth = Math.max(0.65, width / 1800);
      this.snow.forEach((drop, index) => {
        const y = (drop.y + timestamp * (drop.speed * 3.2)) % 1;
        const x = (drop.x + timestamp * 0.000006 + index * 0.0003) % 1;
        context.beginPath(); context.moveTo(x * width, y * height); context.lineTo(x * width - width * 0.008, y * height + height * 0.035); context.stroke();
      });
      if (this.profile.weather === "storm") {
        const flash = Math.max(0, Math.sin(timestamp / 940) - 0.985) * 3.5;
        context.fillStyle = `rgba(255,231,170,${flash})`; context.fillRect(0, 0, width, height);
      }
      context.restore();
    }
  }

  global.GroundGraphAirport = Object.freeze({
    create(canvas, options) { return new AirportSurface(canvas, options); },
    AirportSurface,
  });
})(window);
