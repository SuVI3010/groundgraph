"use strict";

const $ = (id) => document.getElementById(id);
const list = (value) => Array.isArray(value) ? value : [];
const obj = (value) => value && typeof value === "object" && !Array.isArray(value) ? value : {};
const first = (...values) => values.find((value) => value !== undefined && value !== null && value !== "");
const number = (value, fallback = 0) => Number.isFinite(Number(value)) ? Number(value) : fallback;
const escapeHtml = (value) => String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#039;");

const ui = {
  snapshot: null,
  health: null,
  backend: false,
  source: "unavailable",
  unavailableReason: "Waiting for the GroundGraph engine.",
  busy: false,
  playing: false,
  speed: 4,
  visualMinute: 0,
  targetMinute: 0,
  selectedTab: null,
  selectedCycle: null,
  selectedEpisode: null,
  decisionStory: null,
  activeStoryPhase: "trigger",
  storyPhaseEpisode: null,
  storyRequest: 0,
  followLive: true,
  airports: [],
  airportMenuOpen: false,
  map: null,
  tickTimer: null,
  healthTimer: null,
  healthChecking: false,
  healthFailures: 0,
  frame: null,
  lastFrameAt: 0,
  scrubbing: false,
  policyDemo: null,
  brainBusy: false,
};

const UNAVAILABLE_SNAPSHOT = {
  ok: false,
  scenario: {
    id: "ENGINE-UNAVAILABLE", airport: "ORD", airport_name: "Chicago O’Hare International Airport",
    date: "2024-01-12", base_clock_minute: 480, start_minute: 0, end_minute: 240,
    status: "UNAVAILABLE",
  },
  clock: { minute: 0, label: "08:00", cycle: 0, playing: false },
  state_version: 0,
  flights: [], resources: [], events: [], agents: [], handoffs: [],
  causal_path: { summary: "FalkorDB is required to load the operational graph.", nodes: [], edges: [] },
  current_plan: null,
  memory: { episode_count: 0, recalled: [], influence: {} },
  policies: { authorization_complete: false, applicable: [] },
  metrics: { active_delay_minutes: 0, modeled_effect_delay_minutes: 0, projected_delay_minutes: 0, recovered_minutes: 0, plans_proposed: 0, plans_applied: 0 },
  replanning: { status: "ENGINE_UNAVAILABLE", automatic: false, auto_apply: false, interval_minutes: 5, cycle: 0, history: [] },
  graph: { name: "groundgraph", runtime_mode: "unavailable", node_count: 0, edge_count: 0 },
};
function formatClock(relative, snapshot = ui.snapshot) {
  const base = number(snapshot?.scenario?.base_clock_minute, 480);
  const absolute = ((base + number(relative)) % 1440 + 1440) % 1440;
  return `${String(Math.floor(absolute / 60)).padStart(2, "0")}:${String(Math.floor(absolute % 60)).padStart(2, "0")}`;
}
function formatDate(value) {
  const date = new Date(`${String(value || "2024-01-12").slice(0, 10)}T12:00:00Z`);
  return Number.isNaN(date.getTime()) ? String(value || "") : new Intl.DateTimeFormat("en-GB", { day: "2-digit", month: "short", year: "numeric", timeZone: "UTC" }).format(date);
}
function provenance(value) {
  const kind = String(value || "DERIVED").toUpperCase();
  if (kind.includes("MIXED")) return { label: "MIXED", tone: "mixed", description: "Observed source with a modeled operational effect" };
  if (kind.includes("OBSERV")) return { label: "OBSERVED", tone: "observed", description: "Observed public record" };
  if (kind.includes("MODEL") || kind.includes("SYNTH")) return { label: "MODELED", tone: "modeled", description: "Explicit modeled assumption" };
  return { label: "DERIVED", tone: "derived", description: "Derived through a graph query" };
}
function shortStatus(value) { return String(value || "MONITORING").replaceAll("_", " "); }
function coordinatorDisplayName() {
  const health = obj(ui.health); const llm = obj(health.llm);
  const selectedBy = String(ui.snapshot?.current_plan?.selected_by || "").toLowerCase();
  if (selectedBy === "local_model") return "Qwen";
  if (selectedBy.startsWith("deterministic")) return "Safe Coordinator";
  return ui.backend && llm.enabled ? "Model Coordinator" : ui.backend ? "Safe Coordinator" : "Coordinator";
}
function selectionAttribution(value) {
  const key = String(value || "").toLowerCase();
  if (key === "local_model") return { label: "LOCAL QWEN SELECTED", tone: "local-model", detail: "The configured local Qwen model chose from the server-validated, context-bounded safe shortlist." };
  if (key === "deterministic_fallback") return { label: "DETERMINISTIC FALLBACK", tone: "deterministic", detail: "The local model did not return a valid choice, so the safe deterministic ranking selected the candidate." };
  if (key === "deterministic_safe_planner") return { label: "DETERMINISTIC SAFE PLANNER", tone: "deterministic", detail: "The local model was disabled, so the safe deterministic ranking selected the candidate." };
  return { label: key ? `SELECTOR · ${shortStatus(key).toUpperCase()}` : "SELECTOR NOT REPORTED", tone: "neutral", detail: "Selection attribution returned by the GroundGraph API." };
}
function planSafety(plan) {
  const authorization = obj(plan?.policy_authorization); const validation = obj(plan?.safety_validation); const checks = list(validation.checks);
  const passedCount = checks.filter((check) => check === true || obj(check).passed === true || String(obj(check).status || "").toUpperCase() === "PASS").length;
  const knowledgeCurrent = authorization.current_knowledge_complete !== false && authorization.knowledge_current !== false;
  const policyComplete = Boolean(authorization.complete);
  const validationPassed = validation.passed !== false;
  return { authorization, validation, checks, passedCount, knowledgeCurrent, policyComplete, validationPassed, safe: policyComplete && knowledgeCurrent && validationPassed };
}
function coordinatorToolsForPlan(toolTrace, plan) {
  const trace = list(toolTrace); const episodeId = String(plan?.episode_id || "");
  const episodeTools = episodeId ? trace.filter((tool) => String(tool.episode_id || "") === episodeId) : [];
  const legacyTools = trace.filter((tool) => !tool.episode_id && number(tool.cycle) === number(plan?.cycle));
  const matchingTools = episodeTools.length ? episodeTools : episodeId ? legacyTools : trace.filter((tool) => number(tool.cycle) === number(plan?.cycle));
  return matchingTools.sort((a, b) => number(a.sequence) - number(b.sequence)).slice(0, 4);
}
function showToast(message) {
  const toast = $("toast"); toast.textContent = message; toast.hidden = false;
  clearTimeout(showToast.timer); showToast.timer = setTimeout(() => { toast.hidden = true; }, 2800);
}

function requireHealthySnapshot(payload) {
  const snapshot = obj(payload);
  if (snapshot.ok !== true || !snapshot.scenario || !snapshot.clock || !snapshot.graph) {
    throw new Error("The GroundGraph API did not return a healthy graph snapshot.");
  }
  return snapshot;
}

function requireHealthyRuntime(payload) {
  const health = obj(payload);
  if (health.ok !== true || String(health.status || "").toLowerCase() !== "healthy") {
    throw new Error(first(health.detail, health.error, "FalkorDB health check failed."));
  }
  return health;
}

async function request(path, options = {}) {
  const controller = new AbortController(); const timer = setTimeout(() => controller.abort(), options.timeout || 8000);
  try {
    const response = await fetch(path, { ...options, signal: controller.signal, headers: { "Content-Type": "application/json", ...(options.headers || {}) } });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error(payload.error || payload.detail || `Request failed (${response.status})`);
      error.status = response.status; error.payload = payload;
      throw error;
    }
    return payload;
  } finally { clearTimeout(timer); }
}

function normalizeSnapshot(raw) {
  const value = obj(raw);
  const scenario = obj(value.scenario);
  const baseClock = number(first(scenario.base_clock_minute, scenario.start_clock_minute), 480);
  const start = number(scenario.start_minute, 0); const end = number(scenario.end_minute, start + number(scenario.duration_minutes, 240));
  const clock = obj(value.clock);
  const snapshot = {
    ...value,
    scenario: { ...scenario, id: first(scenario.id, "ORD-REPLAY"), airport: first(scenario.airport, "ORD"), airport_name: first(scenario.airport_name, "Chicago O’Hare International Airport"), base_clock_minute: baseClock, start_minute: start, end_minute: end, date: first(scenario.date, "2024-01-12") },
    clock: { ...clock, minute: number(clock.minute, start), cycle: number(first(clock.cycle, value.replanning?.cycle), 0) },
    state_version: number(value.state_version, 1),
    flights: list(value.flights).map((flight) => ({ ...flight, label: first(flight.label, flight.callsign, flight.flight_number, flight.id), minute: number(first(flight.estimated_minute, flight.scheduled_minute, flight.minute), 0), at_risk: number(flight.delay_minutes, 0) > 0 || ["DELAYED", "AT_RISK", "CONFLICT"].includes(String(flight.status || "").toUpperCase()) })),
    resources: list(value.resources), events: list(value.events), agents: list(value.agents), handoffs: list(value.handoffs),
    causal_path: obj(value.causal_path), current_plan: value.current_plan ? obj(value.current_plan) : null,
    memory: { episode_count: 0, recalled: [], influence: {}, ...obj(value.memory) },
    policies: { authorization_complete: false, applicable: [], ...obj(value.policies) },
    metrics: { modeled_effect_delay_minutes: 0, projected_delay_minutes: 0, recovered_minutes: 0, plans_proposed: 0, plans_applied: 0, ...obj(value.metrics) },
    replanning: { status: "MONITORING", automatic: true, auto_apply: false, interval_minutes: 5, cycle: number(clock.cycle, 0), history: [], ...obj(value.replanning) },
    graph: obj(value.graph),
  };
  snapshot.duration = Math.max(1, end - start);
  return snapshot;
}

function activeEvent(snapshot = ui.snapshot) {
  const minute = number(snapshot?.clock?.minute);
  return [...list(snapshot?.events)].filter((event) => number(event.at_minute) <= minute).sort((a, b) => number(b.at_minute) - number(a.at_minute))[0]
    || list(snapshot?.events).sort((a, b) => number(a.at_minute) - number(b.at_minute))[0] || null;
}

function displayedIncidentEvent(snapshot = ui.snapshot) {
  const timelineEvent = activeEvent(snapshot);
  const plan = snapshot?.current_plan;
  const historical = Boolean(plan?.display_only || plan?.historical || plan?.is_current === false);
  if (historical) return timelineEvent;
  const planEvent = list(snapshot?.current_plan?.causal_path?.nodes).find((node) => String(node?.type || "").toUpperCase() === "EVENT");
  if (!planEvent) return timelineEvent;
  const current = list(snapshot?.events).find((event) => String(event.id) === String(planEvent.id));
  if (current && number(current.at_minute) <= number(snapshot?.clock?.minute)) return { ...planEvent, ...current };
  return timelineEvent;
}

function formatRuntimeTime(value) {
  if (!value) return "not reported";
  const date = new Date(value); if (Number.isNaN(date.getTime())) return String(value);
  const parts = new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }).format(date);
  return `${parts}.${String(date.getMilliseconds()).padStart(3, "0")}`;
}

function renderRuntime() {
  const snapshot = ui.snapshot; const health = obj(ui.health); const graph = obj(snapshot.graph);
  const truth = $("truth-badge"); truth.textContent = ui.backend ? "LIVE GRAPH STATE" : "ENGINE UNAVAILABLE"; truth.classList.toggle("live", ui.backend); truth.classList.toggle("unavailable", !ui.backend);
  const graphStatus = $("graph-status"); graphStatus.className = `runtime-pill ${ui.backend ? "ready" : "warn"}`;
  graphStatus.querySelector("small").textContent = ui.backend ? `${first(graph.runtime_mode, health.runtime_mode, "server")} · ${number(graph.node_count, number(health.node_count, 0))} nodes` : "required to run";
  const modelStatus = $("model-status"); const llm = obj(health.llm); const modelConfigured = ui.backend && llm.enabled === true;
  modelStatus.className = `runtime-pill ${modelConfigured ? "configured" : "warn"}`;
  modelStatus.querySelector("strong").textContent = modelConfigured ? "Local Qwen" : ui.backend ? "Safe planner" : "Coordinator offline";
  modelStatus.querySelector("small").textContent = modelConfigured ? `${first(llm.model, "Ollama")} · verify on plan` : ui.backend ? "deterministic ranking" : "engine required";
  $("coordinator-name").textContent = `${coordinatorDisplayName()}${coordinatorDisplayName() === "Qwen" ? " Coordinator" : ""}`;
}

function renderScenario() {
  const snapshot = ui.snapshot; const scenario = snapshot.scenario;
  $("airport-code").textContent = scenario.airport;
  $("airport-name").textContent = String(scenario.airport_name || "Chicago O’Hare").replace(" International Airport", "");
  $("scenario-label").textContent = `Historical operations replay · ${formatDate(scenario.date)}`;
  const zone = first(scenario.timezone_abbreviation, ({ ORD: "CT", ATL: "ET", SFO: "PT" })[scenario.airport], "LT");
  $("window-label").textContent = `${formatClock(scenario.start_minute)}–${formatClock(scenario.end_minute)} ${zone}`;
  $("clock-zone").textContent = zone;
  const surfaceTitle = $("surface-title"); if (surfaceTitle) surfaceTitle.textContent = `${scenario.airport} operations map`;
  $("timeline-start").textContent = formatClock(scenario.start_minute);
  $("timeline-end").textContent = formatClock(scenario.end_minute);
  const baggage = list(snapshot.events).find((event) => String(event.type).includes("BAG"));
  const firstDecision = Math.max(60, number(baggage?.at_minute, 36) + 5);
  $("event-marker").textContent = `${formatClock(number(baggage?.at_minute, 36))} disruption`;
  $("event-marker").style.left = `${Math.max(20, clampPercent(number(baggage?.at_minute, 36) / snapshot.duration))}%`;
  $("decision-marker").textContent = `${formatClock(firstDecision)} decision`;
  $("decision-marker").style.left = `${clampPercent(firstDecision / snapshot.duration)}%`;
  $("timeline").max = String(snapshot.duration); $("timeline").min = String(scenario.start_minute);
}

function clampPercent(value) { return Math.max(0, Math.min(100, value * 100)); }

function renderWeather() {
  const snapshot = ui.snapshot; const weather = [...snapshot.events].filter((event) => ["LOW_VISIBILITY_SNOW", "WEATHER_UPDATE", "WEATHER", "WIND"].includes(String(event.type).toUpperCase()) && number(event.at_minute) <= snapshot.clock.minute).sort((a, b) => number(b.at_minute) - number(a.at_minute))[0];
  if (!weather) { $("weather-primary").textContent = "Awaiting the next observation"; $("weather-secondary").textContent = "No future data is visible"; return; }
  const weatherLabel = String(first(weather.description, weather.summary, weather.label, ""));
  const type = String(weather.type).toUpperCase();
  const visibilityMatch = weatherLabel.match(/(\d+(?:\.\d+)?|\d+\s*\/\s*\d+)\s*miles?/i);
  const gustMatch = weatherLabel.match(/gust(?:ing|s)?(?:\s+to)?\s+(\d+)\s*knots?/i);
  const compactFraction = (value) => ({ "1/4": "¼", "1/2": "½", "3/4": "¾" })[String(value).replaceAll(" ", "")] || value;
  const condition = type.includes("SNOW") ? "Snow" : /thunder|lightning/i.test(weatherLabel) ? "Thunderstorm" : /heavy rain/i.test(weatherLabel) ? "Heavy rain" : type === "WIND" ? "Gusty winds" : /drizzle/i.test(weatherLabel) ? "Low ceiling" : "Weather update";
  const measure = visibilityMatch ? `${compactFraction(visibilityMatch[1])} mi visibility` : gustMatch ? `gusts ${gustMatch[1]} kt` : "time-gated observation";
  $("weather-primary").textContent = `${condition} · ${measure}`;
  const station = first(snapshot.scenario.weather_station, snapshot.scenario.icao, `${String(snapshot.scenario.airport).length === 3 ? "K" : ""}${snapshot.scenario.airport}`);
  $("weather-secondary").textContent = `${formatClock(weather.at_minute)} ${$("clock-zone").textContent} · ${station} source observation`;
}

function renderIncident() {
  const snapshot = ui.snapshot; const event = displayedIncidentEvent(snapshot); if (!event) return;
  const mitigated = ["MITIGATED", "RESOLVED", "CLOSED"].includes(String(event.status || "").toUpperCase());
  $("incident-kicker").textContent = mitigated ? "LATEST EVENT · MITIGATED" : "ACTIVE DISRUPTION";
  const tag = $("incident-provenance"); const source = provenance(event.source_kind); tag.className = `provenance-tag ${source.tone}`; tag.textContent = source.label; tag.title = source.description;
  const titleByType = {
    BAG_RECONCILIATION_DELAY: "Baggage reconciliation delay entering the graph",
    LOW_VISIBILITY_SNOW: "Snow and low visibility constraining arrival flow",
    WEATHER_UPDATE: "Observed weather improvement triggered a modeled capacity update.",
    RUNWAY_OCCUPANCY_CHECK: "Runway occupancy inspection reducing throughput",
  };
  $("incident-title").textContent = titleByType[event.type] || String(event.type || "Operational update").replaceAll("_", " ");
  $("incident-detail").textContent = event.summary || "Connected state changed at the current replay time.";
  const eventTag = $("event-provenance"); eventTag.className = `provenance-tag ${source.tone}`; eventTag.textContent = source.label; eventTag.title = source.description;
  $("event-time").textContent = formatClock(event.at_minute);
  $("event-title").textContent = $("incident-title").textContent;
  $("event-detail").textContent = event.summary || "The event is visible to the agent swarm.";
  $("event-symbol").textContent = mitigated ? "✓" : String(event.severity).toUpperCase() === "HIGH" ? "!" : "↗";
  const severity = document.querySelector(".severity-symbol"); if (severity) severity.textContent = mitigated ? "✓" : "!";
  const callout = $("event-callout"); callout.classList.toggle("risk", ["HIGH", "MEDIUM"].includes(String(event.severity).toUpperCase()) && !mitigated);
  callout.classList.toggle("success", mitigated);
}

function renderMetrics() {
  const metrics = ui.snapshot.metrics;
  const displayedPlan = ui.snapshot.current_plan;
  const historical = Boolean(displayedPlan?.display_only || displayedPlan?.historical || displayedPlan?.is_current === false);
  const plan = historical ? null : displayedPlan;
  const applied = ["APPLIED", "EXECUTING", "COMPLETED"].includes(String(plan?.status || "").toUpperCase());
  const activeBaseline = number(first(metrics.active_delay_minutes, metrics.modeled_effect_delay_minutes), 0);
  const appliedBaseline = plan?.baseline_delay !== undefined && plan?.baseline_delay !== null
    ? number(plan.baseline_delay, activeBaseline)
    : number(plan?.projected_delay, 0) + number(plan?.recovered_minutes, 0);
  const before = applied ? appliedBaseline : activeBaseline;
  const after = plan ? number(first(plan.projected_delay, metrics.projected_delay_minutes), before) : null;
  const currentlyAffected = Math.max(0, ui.snapshot.flights.filter((flight) => flight.at_risk || number(flight.delay_minutes) > 0 || ["DELAYED", "AT_RISK", "CONFLICT"].includes(String(flight.status || "").toUpperCase())).length);
  const pathFlights = new Set(list(plan?.causal_path?.nodes)
    .filter((node) => String(node?.type || "").toUpperCase().includes("FLIGHT"))
    .map((node) => node.id)
    .filter(Boolean));
  const affected = plan && before > 0 ? Math.max(currentlyAffected, pathFlights.size) : currentlyAffected;
  const projectedAffected = plan ? (after <= 0 ? 0 : currentlyAffected) : null;
  const gateBefore = before ? Math.max(4, Math.round(before * 0.65)) : 0; const gateAfter = after === null ? null : Math.max(0, Math.round(after * 0.45));
  $("delay-before").textContent = before ? `${before}m` : "—"; $("delay-after").textContent = after === null ? "—" : `${after}m`;
  $("delay-delta").textContent = after === null ? "awaiting a material disruption" : `${Math.max(0, before - after)} min projected recovery`;
  $("flights-before").textContent = affected || "—"; $("flights-after").textContent = projectedAffected === null ? "—" : projectedAffected;
  $("flights-delta").textContent = affected ? "derived from connected rotation path" : "no active impact path";
  $("gate-before").textContent = gateBefore ? `${gateBefore}m` : "—"; $("gate-after").textContent = gateAfter === null ? "—" : `${gateAfter}m`;
  $("gate-delta").textContent = "modeled where public telemetry is unavailable";
  const safety = planSafety(plan); const authorization = plan ? safety.policyComplete && safety.knowledgeCurrent : Boolean(ui.snapshot.policies.authorization_complete);
  const currentVersion = applied || number(plan?.based_on_version) === ui.snapshot.state_version;
  const checkLabel = safety.checks.length ? `${safety.passedCount} / ${safety.checks.length}` : safety.safe ? "VERIFIED" : "BLOCKED";
  $("checks-value").textContent = plan ? (authorization && currentVersion && safety.validationPassed ? checkLabel : "BLOCKED") : "WAITING";
  $("checks-detail").textContent = plan ? (!safety.knowledgeCurrent ? "policy knowledge is stale" : authorization && safety.validationPassed ? "policy + current graph verified" : "current authority incomplete") : historical ? "previous decision retained in memory" : "validated before every apply";
  const outcomePlan = displayedPlan;
  const outcomeBefore = outcomePlan ? number(first(outcomePlan.baseline_delay, metrics.baseline_delay_minutes), 0) : 0;
  const outcomeAfter = outcomePlan ? number(first(outcomePlan.projected_delay, metrics.projected_delay_minutes), outcomeBefore) : null;
  const outcomeGateBefore = outcomeBefore ? Math.max(4, Math.round(outcomeBefore * 0.65)) : 0;
  const outcomeGateAfter = outcomeAfter === null ? null : Math.max(0, Math.round(outcomeAfter * 0.45));
  const outcomeMax = Math.max(outcomeBefore, 1);
  $("bar-delay-value").textContent = outcomeAfter === null ? "waiting" : `${outcomeBefore} → ${outcomeAfter} min`;
  $("bar-gate-value").textContent = outcomeGateAfter === null ? "waiting" : `${outcomeGateBefore} → ${outcomeGateAfter} min`;
  document.querySelector("#comparison-bars > div:first-child .projected").style.width = outcomeAfter === null ? "0%" : `${Math.max(5, outcomeAfter / outcomeMax * 100)}%`;
  document.querySelector("#comparison-bars > div:nth-child(2) .projected").style.width = outcomeGateAfter === null ? "0%" : `${Math.max(5, outcomeGateAfter / Math.max(outcomeGateBefore, 1) * 100)}%`;
  const recovered = Math.max(0, number(outcomePlan?.recovered_minutes, 0));
  $("outcome-improvement").textContent = recovered ? historical ? `PREVIOUS RUN · −${recovered} MIN` : `−${recovered} MIN PROJECTED` : "AWAITING PLAN";
  $("outcome-title").textContent = recovered ? historical ? `Last applied replay projected ${recovered} minutes recovered` : `${recovered} minutes recovered in the bounded replay` : "Counterfactual opens after a safe plan";
}

const AGENT_KEYS = { "AGENT-WEATHER": "weather", "AGENT-FLOW": "flow", "AGENT-GATE": "gate", "AGENT-TURN": "turn", "AGENT-POLICY": "policy", "AGENT-MEMORY": "memory", "AGENT-COORD": "coordinator" };
function renderAgents() {
  const snapshot = ui.snapshot; const byKey = {};
  snapshot.agents.forEach((agent) => { const key = AGENT_KEYS[agent.id] || Object.values(AGENT_KEYS).find((name) => String(agent.name || "").toLowerCase().includes(name)); if (key) byKey[key] = agent; });
  Object.entries({ weather: "Watching observations", flow: "Watching capacity", gate: "Watching resources", turn: "Watching tasks", policy: "Current procedure loaded", memory: "Watching prior outcomes", coordinator: "Waiting for findings" }).forEach(([key, fallback]) => {
    const agent = byKey[key] || {}; const card = document.querySelector(`[data-agent="${key}"]`); if (!card) return;
    const status = String(agent.status || "MONITORING").toUpperCase(); card.classList.remove("active", "complete", "risk");
    if (["RUNNING", "REPLANNING", "COORDINATING", "READING", "ANALYZING"].some((word) => status.includes(word))) card.classList.add("active");
    else if (["ERROR", "ESCALATED", "BLOCKED"].some((word) => status.includes(word))) card.classList.add("risk");
    else if (number(agent.last_cycle) > 0 || ["COMPLETE", "CONSUMED", "AWAITING_APPROVAL"].some((word) => status.includes(word))) card.classList.add("complete");
    const message = first(agent.current_task, agent.role, fallback); const target = $(`${key === "turn" ? "turn" : key}-agent-message`) || (key === "coordinator" ? $("coordinator-message") : null);
    if (target) target.textContent = message;
  });
  const validator = document.querySelector('[data-agent="safety"]'); validator?.classList.remove("active", "complete", "risk");
  const plan = snapshot.current_plan; const applied = ["APPLIED", "EXECUTING", "COMPLETED"].includes(String(plan?.status || "").toUpperCase()); const safety = planSafety(plan);
  const valid = safety.safe && (applied || number(plan?.based_on_version) === snapshot.state_version);
  if (ui.busy) { validator?.classList.add("active"); $("safety-agent-message").textContent = "Checking current graph"; }
  else if (plan && valid) { validator?.classList.add("complete"); $("safety-agent-message").textContent = plan.display_only || plan.historical || plan.is_current === false ? "Validated when applied" : safety.checks.length ? `${safety.passedCount}/${safety.checks.length} checks passed` : "Current knowledge verified"; }
  else if (plan) { validator?.classList.add("risk"); $("safety-agent-message").textContent = "Plan blocked"; }
  else $("safety-agent-message").textContent = "Guardrails ready";
  const latest = [...snapshot.handoffs].sort((a, b) => number(b.cycle) - number(a.cycle))[0];
  $("handoff-route").textContent = latest ? `${agentLabel(latest.from_agent)} → FalkorDB → ${agentLabel(latest.to_agent)}` : `6 agents → FalkorDB → ${coordinatorDisplayName()} → validator`;
  $("handoff-message").textContent = latest?.summary || "Every finding is versioned before selection.";
  $("graph-version").textContent = `v${snapshot.state_version}`;
}
function agentLabel(id) { return String(id || "Agent").replace("AGENT-", "").replace("COORD", coordinatorDisplayName()).replaceAll("_", " "); }

const SPECIALIST_ORDER = ["WEATHER", "FLOW", "GATE", "TURN", "POLICY", "MEMORY"];
function specialistKey(handoff) { return String(handoff?.from_agent || "").replace("AGENT-", "").toUpperCase(); }
function handoffCard(handoff) {
  const key = specialistKey(handoff); const evidence = list(handoff.evidence_ids); const inputVersion = first(handoff.input_state_version, handoff.based_on_version, handoff.state_version, "—");
  const started = first(handoff.started_at, handoff.created_at); const ended = first(handoff.ended_at, handoff.completed_at, handoff.created_at);
  const query = first(handoff.query_name, handoff.query, `${key.toLowerCase()} graph read`);
  return `<article class="handoff-card" data-agent="${escapeHtml(key)}"><span class="handoff-agent">${escapeHtml(key.slice(0, 1) || "A")}</span><span class="handoff-body"><strong>${escapeHtml(agentLabel(handoff.from_agent))} → Coordinator</strong><small>${escapeHtml(humanize(query))}</small><span class="handoff-meta"><span>input v${escapeHtml(inputVersion)}</span><span>${escapeHtml(formatRuntimeTime(started))} → ${escapeHtml(formatRuntimeTime(ended))}</span></span><span class="handoff-evidence">${evidence.length ? evidence.map((id) => `<span>${escapeHtml(id)}</span>`).join("") : '<span class="empty">no evidence IDs returned</span>'}</span></span><em class="handoff-status">${escapeHtml(shortStatus(first(handoff.status, "RECORDED")).toUpperCase())}</em></article>`;
}

function renderReplanning() {
  const snapshot = ui.snapshot; const replanning = snapshot.replanning; const cycle = number(first(replanning.cycle, snapshot.clock.cycle)); const history = list(replanning.history);
  $("cycle-number").textContent = String(cycle).padStart(2, "0"); $("cycle-count").textContent = history.length;
  const radar = $("replan-radar"); radar.classList.toggle("running", ui.busy || String(replanning.status).toUpperCase().includes("REPLAN"));
  $("replan-state").textContent = ui.busy ? "Specialists are reading FalkorDB" : shortStatus(replanning.status || "MONITORING");
  const next = number(replanning.next_checkpoint_minute, snapshot.clock.minute + number(replanning.interval_minutes, 5)); const remaining = Math.max(0, next - snapshot.clock.minute);
  $("replan-next").textContent = ui.busy ? `Cycle ${cycle + 1} in progress` : `Next check in ${remaining} replay min`;
  const autoApply = replanning.auto_apply === true; const toggle = $("auto-apply"); toggle.checked = autoApply;
  $("auto-apply-state").textContent = autoApply ? "ON" : "OFF";
  const orderedHistory = [...history];
  const handoffCycles = snapshot.handoffs.map((handoff) => number(handoff.cycle)).filter((value) => value > 0);
  const latestCycle = first(orderedHistory[0]?.cycle, handoffCycles.length ? Math.max(...handoffCycles) : null);
  const historyKey = (item) => first(item?.episode_id, item?.id, item?.plan_id, item?.cycle);
  const latestSelection = first(historyKey(orderedHistory[0]), latestCycle);
  const validSelection = orderedHistory.some((item) => String(historyKey(item)) === String(ui.selectedCycle)) || (!orderedHistory.length && handoffCycles.some((item) => String(item) === String(ui.selectedCycle)));
  if (ui.selectedCycle === null || !validSelection) ui.selectedCycle = latestSelection;
  const selected = orderedHistory.find((item) => String(historyKey(item)) === String(ui.selectedCycle)) || orderedHistory[0] || null;
  const selectedCycle = first(selected?.cycle, latestCycle);
  const selectedEpisode = first(selected?.episode_id, selected?.id);
  const selectedHandoffs = snapshot.handoffs.filter((handoff) => selectedEpisode && handoff.episode_id ? String(handoff.episode_id) === String(selectedEpisode) : String(handoff.cycle) === String(selectedCycle)).sort((a, b) => SPECIALIST_ORDER.indexOf(specialistKey(a)) - SPECIALIST_ORDER.indexOf(specialistKey(b)));
  const missing = SPECIALIST_ORDER.filter((key) => !selectedHandoffs.some((handoff) => specialistKey(handoff) === key));
  const selector = orderedHistory.length ? orderedHistory.map((item) => {
    const status = String(first(item.status, item.result, "NO_CHANGE")).toUpperCase(); const tone = status.includes("APPLIED") ? "applied" : status.includes("NO_") || status.includes("MONITOR") || status.includes("FROZEN") ? "no-change" : "";
    const selectionKey = historyKey(item);
    return `<button type="button" class="cycle-row ${tone} ${String(selectionKey) === String(historyKey(selected)) ? "latest" : ""}" data-cycle="${escapeHtml(item.cycle)}" data-episode="${escapeHtml(selectionKey)}"><i></i><span><strong>Cycle ${String(number(item.cycle)).padStart(2, "0")} · ${escapeHtml(status.replaceAll("_", " "))}</strong><small>${escapeHtml(first(item.summary, "Graph state checked; no material plan change."))}</small></span><em>${escapeHtml(String(first(item.trigger, "PERIODIC")).replaceAll("_", " "))}</em></button>`;
  }).join("") : "";
  $("cycles-stage").innerHTML = selectedCycle !== null && selectedCycle !== undefined ? `<section class="cycle-browser"><div class="cycle-selector">${selector}</div><section class="cycle-inspector"><header><span><strong>Six specialist handoffs · cycle ${String(number(selectedCycle)).padStart(2, "0")}</strong><small>${escapeHtml(first(selected?.summary, "Persisted findings shared through FalkorDB before coordination."))}</small></span><em>${selectedHandoffs.length}/6 RETURNED</em></header><div class="handoff-grid">${selectedHandoffs.map(handoffCard).join("")}${missing.length ? `<div class="handoff-missing">Awaiting persisted handoff${missing.length === 1 ? "" : "s"}: ${escapeHtml(missing.map(humanize).join(", "))}</div>` : ""}</div></section></section>` : '<div class="empty-detail">No server-returned replanning cycles yet.</div>';
}

function planActions(plan) { return list(plan?.actions); }
function candidateStrategyLabel(candidateId) {
  const value = String(candidateId || "").replace(/^CAND-\d+-/, "");
  return value ? humanize(value) : "validated candidate";
}
function renderPlan() {
  const snapshot = ui.snapshot; const plan = snapshot.current_plan; const actions = planActions(plan);
  $("plan-count").textContent = actions.length; $("waiting-state").hidden = Boolean(plan); $("plan-stage").hidden = !plan;
  if (!plan) {
    $("decision-envelope").innerHTML = "";
    $("waiting-kicker").textContent = `WATCHING ${number(snapshot.graph.node_count, snapshot.flights.length + snapshot.resources.length)} CONNECTED ENTITIES`;
    $("waiting-title").textContent = snapshot.clock.minute < 36 ? "No intervention needed yet" : "No material plan change";
    $("waiting-copy").textContent = "Five-minute and event-driven cycles may return NO_CHANGE. A plan appears only when the graph finds a safe, material improvement.";
    updatePrimaryAction(); return;
  }
  const status = String(plan.status || "PROPOSED").toUpperCase(); const safety = planSafety(plan); const authorized = safety.safe;
  const historical = Boolean(plan.display_only || plan.historical || plan.is_current === false);
  $("plan-kicker").textContent = historical ? "LATEST APPLIED PLAN · HISTORY" : status === "APPLIED" ? "APPLIED IN REPLAY" : authorized ? "GRAPH-SAFE PLAN" : "OPERATOR REVIEW";
  $("plan-title").textContent = first(plan.strategy ? humanize(plan.strategy) : null, status === "APPLIED" ? "Recovery executing" : "Recovery plan ready");
  const selection = selectionAttribution(plan.selected_by); const selector = $("plan-selector");
  selector.textContent = selection.label; selector.className = `selector-badge ${selection.tone}`; selector.title = selection.detail;
  $("plan-score").textContent = safety.checks.length ? `${safety.passedCount}/${safety.checks.length} checks` : authorized ? "checks verified" : !safety.knowledgeCurrent ? "stale knowledge" : "authorization incomplete";
  $("action-list").innerHTML = actions.map((action, index) => `<article class="action-item" data-flight="${escapeHtml(action.target_id || "")}"><i>${index + 1}</i><span><strong>${escapeHtml(first(action.description, humanize(action.type), `Recovery action ${index + 1}`))}</strong><small>${escapeHtml([humanize(action.type), action.policy_id ? `authorized by ${action.policy_id}` : "policy evidence required"].filter(Boolean).join(" · "))}</small></span><em>${status === "APPLIED" ? "APPLIED" : "VALIDATED"}</em></article>`).join("");
  const influence = obj(snapshot.memory.influence); const explanation = first(plan.summary, "Selected from the server-validated safe set using the current graph state.");
  const baseLeader = candidateStrategyLabel(first(influence.base_ranking_winner_id, influence.base_winner_id));
  const adjustedLeader = candidateStrategyLabel(first(influence.memory_adjusted_ranking_winner_id, influence.base_ranking_winner_id));
  const finalChoice = candidateStrategyLabel(first(influence.final_candidate_id, plan.candidate_id));
  const rankingNote = influence.ranking_changed
    ? `Memory shifted the deterministic ranking from ${baseLeader} to ${adjustedLeader}.`
    : influence.applied_to_ranking || influence.applied
      ? `Context-matched memory adjusted candidate cost by ${number(influence.adjustment)} without changing the deterministic leader.`
      : "No prior outcome changed the deterministic ranking.";
  const choiceNote = String(plan.selected_by).toLowerCase() === "local_model"
    ? influence.final_choice_matched_adjusted_ranking
      ? `Local Qwen confirmed the memory-adjusted leader: ${finalChoice}.`
      : `Local Qwen made the final allowlisted choice: ${finalChoice}.`
    : `The deterministic safe planner made the final choice: ${finalChoice}.`;
  $("plan-explanation").textContent = `${explanation} ${rankingNote} ${choiceNote}`;
  const selectedId = String(first(plan.candidate_id, influence.final_candidate_id, ""));
  const modelIds = new Set(list(plan.model_candidate_set).map((item) => String(item.id)));
  const candidates = list(plan.safe_candidate_set);
  const candidateRows = candidates.map((candidate) => {
    const chosen = String(candidate.id) === selectedId; const considered = modelIds.has(String(candidate.id));
    const disposition = chosen ? "SELECTED" : considered ? "MODEL SHORTLIST" : "WITHHELD BY CONTEXT WINDOW";
    return `<span class="candidate-row ${chosen ? "chosen" : ""} ${considered ? "model-shortlist" : "context-withheld"}"><b>${escapeHtml(humanize(candidate.strategy))}</b><small>${number(candidate.recovered_minutes)}m recovery · ${number(candidate.burden_minutes)}m coordination</small><em>${number(candidate.cost_score)} cost · ${disposition}</em></span>`;
  }).join("");
  const tools = coordinatorToolsForPlan(snapshot.tool_trace, plan);
  const toolRows = tools.map((tool) => `<span title="${escapeHtml(first(tool.result_summary, "Graph check completed"))}">✓ ${escapeHtml(humanize(tool.name))}</span>`).join("");
  $("decision-envelope").innerHTML = `${candidates.length ? `<section><header><b>SAFE NON-DOMINATED RANKING</b><small>${candidates.length} safe · ${modelIds.size} sent to Qwen · lower cost is better</small></header>${candidateRows}</section>` : ""}${tools.length ? `<section><header><b>COORDINATOR CHECKS</b><small>persisted tool trace</small></header><div class="tool-chips">${toolRows}</div></section>` : ""}`;
  updatePrimaryAction();
}
function humanize(value) { return String(value || "").replaceAll("_", " ").toLowerCase().replace(/\b\w/g, (letter) => letter.toUpperCase()); }

function currentGatePolicy(snapshot = ui.snapshot) {
  return list(snapshot?.policies?.applicable).find((item) => {
    const searchable = `${item.id || ""} ${item.title || ""} ${list(item.action_types).join(" ")}`.toUpperCase();
    return item.current !== false && String(item.status || "CURRENT").toUpperCase() !== "SUPERSEDED" && (searchable.includes("GATE") || searchable.includes("REASSIGN_GATE"));
  }) || null;
}

function incrementPolicyVersion(value) {
  const parts = String(value || "1.0").split(".");
  const last = Number(parts.at(-1));
  if (Number.isFinite(last)) parts[parts.length - 1] = String(last + 1);
  else parts.push("1");
  return parts.join(".");
}

function policyDemoMarkup(policy) {
  if (!policy) return "";
  const demo = ui.policyDemo;
  if (demo) {
    return `<section class="policy-demo-control complete"><span class="policy-demo-icon">✓</span><span><small>SYNTHETIC POLICY UPDATE PUBLISHED</small><strong>${escapeHtml(demo.oldId)} → ${escapeHtml(demo.newId)}</strong><em>Graph v${demo.oldState} → v${demo.newState} · stale proposals invalidated · cycle ${String(demo.cycle).padStart(2, "0")}</em></span><button type="button" class="view-policy-cycle">View cycle</button></section>`;
  }
  const nextVersion = incrementPolicyVersion(policy.version);
  return `<section class="policy-demo-control"><span class="policy-demo-icon">P</span><span><small>SYNTHETIC COMPANY-BRAIN DEMO</small><strong>Publish gate policy v${escapeHtml(nextVersion)}</strong><em>Preserves ${escapeHtml(first(policy.owner, "owner"))} and its source; FalkorDB supersedes v${escapeHtml(first(policy.version, "current"))} and replans.</em></span><button type="button" class="publish-policy-update" data-policy-id="${escapeHtml(policy.id)}">Publish update</button></section>`;
}

function renderEvidenceAndMemory() {
  const snapshot = ui.snapshot; const plan = snapshot.current_plan; const policyEvidence = list(plan?.policy_authorization?.evidence);
  const applicable = list(snapshot.policies.applicable).filter((item) => item.current !== false && String(item.status || "CURRENT") !== "SUPERSEDED");
  const currentPolicyIds = new Set(applicable.flatMap((item) => [item.id, item.policy_id].filter(Boolean).map(String)));
  const historicalPlan = Boolean(plan?.display_only || plan?.historical || plan?.is_current === false);
  const evidence = policyEvidence.length ? policyEvidence : applicable;
  $("evidence-count").textContent = evidence.length;
  const policyControl = policyDemoMarkup(currentGatePolicy(snapshot));
  const pathIds = new Set(list(snapshot.causal_path?.nodes).map((node) => String(node.id)));
  const provenanceRecords = list(snapshot.provenance?.records).filter((record) => pathIds.has(String(record.entity_id))).slice(0, 6);
  const provenanceRows = provenanceRecords.length ? `<section class="record-provenance"><header><small>FIELD-LEVEL PROVENANCE</small><strong>Public records and modeled boundaries</strong></header>${provenanceRecords.map((record) => {
    const observed = list(record.observed_fields); const modeled = list(record.modeled_fields);
    return `<details class="evidence-card ${String(record.truth_label).toUpperCase().includes("MODEL") ? "modeled" : "current"}"><summary><i></i><span><strong>${escapeHtml(record.entity_id)}</strong><small>${escapeHtml(first(record.source_label, record.source_id, "Source record"))} · ${escapeHtml(record.source_record_id)}</small></span><em>${escapeHtml(shortStatus(record.relationship))}</em></summary><div class="evidence-detail"><span><b>Observed fields</b>${escapeHtml(observed.length ? observed.join(", ") : "none")}</span><span><b>Modeled fields</b>${escapeHtml(modeled.length ? modeled.join(", ") : "none")}</span><span><b>Stable record ID</b>${escapeHtml(record.record_node_id)}</span></div></details>`;
  }).join("")}</section>` : "";
  const evidenceRows = evidence.length ? evidence.map((item) => {
    const title = first(item.title, item.policy_id, item.id, "Current procedure");
    const owner = first(item.owner, item.owner_team, "Named owner unavailable");
    const source = first(item.source, item.source_document, "Source linked in FalkorDB");
    const version = first(item.version, item.policy_version, item.policy_id ? "" : "current");
    const role = first(item.responsible_role, item.responsible_role_id, item.role, "Not reported");
    const section = first(item.source_section, item.section, "Not reported");
    const evidencePolicyId = first(item.policy_id, item.id, "");
    const supersededSinceApply = historicalPlan && evidencePolicyId && currentPolicyIds.size > 0 && !currentPolicyIds.has(String(evidencePolicyId));
    const freshness = supersededSinceApply ? "CURRENT AT APPLY · SUPERSEDED NOW" : String(first(item.freshness_status, item.knowledge_current === false ? "STALE_FOR_REPLAY" : item.status, item.authorized === false ? "BLOCKED" : "CURRENT")).toUpperCase();
    const freshnessTone = supersededSinceApply ? "superseded" : item.knowledge_current === false || freshness.includes("STALE") || freshness.includes("BLOCK") ? "stale" : "current";
    const excerpt = first(item.source_excerpt, item.excerpt, "No source excerpt returned by the API.");
    const procedure = first(item.procedure_id, item.procedure_version_id, "");
    return `<details class="evidence-card ${freshnessTone}"><summary><i></i><span><strong>${escapeHtml(title)}</strong><small>${escapeHtml([owner, source, version].filter(Boolean).join(" · "))}</small></span><em>${escapeHtml(shortStatus(freshness))}</em></summary><div class="evidence-detail"><span><b>Responsible role</b>${escapeHtml(role)}</span><span><b>Source section</b>${escapeHtml(section)}</span>${procedure ? `<span><b>Procedure link</b>${escapeHtml(procedure)}</span>` : ""}<span class="evidence-excerpt"><b>Source excerpt</b><q>${escapeHtml(excerpt)}</q></span></div></details>`;
  }).join("") : '<div class="empty-detail">Policy and provenance evidence appears with a proposed plan.</div>';
  $("evidence-stage").innerHTML = policyControl + provenanceRows + evidenceRows;
  const recalled = list(snapshot.memory.recalled); $("memory-count").textContent = number(snapshot.memory.episode_count, recalled.length);
  const influenceIds = new Set(list(snapshot.memory.influence?.memory_ids).map(String));
  $("memory-stage").innerHTML = recalled.length ? recalled.map((item) => {
    const context = obj(item.context); const flights = list(first(item.flight_ids, context.flight_ids)); const resources = list(first(item.resource_ids, context.resource_ids)); const rotations = list(first(item.rotation_ids, context.rotation_ids));
    const matched = influenceIds.has(String(item.id));
    const contextLine = [first(item.event_class, context.event_class, "prior disruption"), flights.length ? `flights ${flights.join(", ")}` : null, rotations.length ? `rotation ${rotations.join(", ")}` : null, resources.length ? `resources ${resources.join(", ")}` : null].filter(Boolean).join(" · ");
    return `<details class="memory-row ${matched ? "matched" : ""}"><summary><i></i><span><strong>${escapeHtml(first(item.strategy, item.title, item.id, "Prior recovery"))}</strong><small>${escapeHtml(`${first(item.outcome_status, item.status, "SUCCESS")} · ${number(item.benefit_minutes)} projected min · ${contextLine}`)}</small></span><em>${matched ? "USED THIS CYCLE" : "STORED"}</em></summary><div class="memory-detail"><span><b>Memory</b>${escapeHtml(item.id)}</span><span><b>Plan → outcome</b>${escapeHtml(first(item.plan_id, "linked plan"))} → ${escapeHtml(first(item.outcome_id, "linked outcome"))}</span><span><b>Confidence</b>${escapeHtml(first(item.confidence, "SIMULATED"))}</span></div></details>`;
  }).join("") : `<div class="empty-detail">No prior outcome yet.<br />An applied plan becomes shared episodic memory for the next run.</div>`;
}

function curatedPath(snapshot = ui.snapshot) {
  const returned = list(snapshot.causal_path?.nodes); if (returned.length) {
    if (returned.length <= 6) return returned;
    const types = ["EVENT", "FLIGHT", "RESOURCE", "ACTION", "ACTION", "POLICY"]; const selected = [];
    types.forEach((type) => { const match = returned.find((node) => String(node.type).toUpperCase() === type && !selected.includes(node)); if (match) selected.push(match); });
    returned.forEach((node) => { if (selected.length < 6 && !selected.includes(node)) selected.push(node); });
    return selected.slice(0, 6);
  }
  return [];
}
function renderCausalPath() {
  const nodes = curatedPath(); const returnedEdges = list(ui.snapshot.causal_path?.edges); $("causal-title").textContent = first(ui.snapshot.causal_path?.summary, "Disruption → operational impact → safe action");
  $("causal-path").innerHTML = nodes.length ? nodes.map((node, index) => {
    const source = provenance(first(node.source_kind, ["EVENT", "FLIGHT"].includes(String(node.type).toUpperCase()) ? "OBSERVED" : "DERIVED"));
    const status = String(node.status || "").toUpperCase(); const tone = status.includes("RISK") || status.includes("ACTIVE") || status.includes("DELAY") ? "risk" : String(node.type).toUpperCase() === "ACTION" ? (status === "APPLIED" ? "resolved" : "action") : status.includes("MITIGAT") || status.includes("RECOVER") ? "resolved" : "";
    const next = nodes[index + 1]; const linkedNext = next && returnedEdges.some((edge) => String(edge.source) === String(node.id) && String(edge.target) === String(next.id));
    return `<button type="button" class="causal-node ${source.tone} ${tone} ${linkedNext ? "linked-next" : ""} ${index === 0 ? "active" : ""}" data-node="${escapeHtml(node.id)}" data-detail="${escapeHtml(`${node.label} · ${humanize(node.type)} · ${shortStatus(node.status)} · ${source.label}`)}"><span>${escapeHtml(humanize(node.type))}</span><strong>${escapeHtml(node.label)}</strong><em title="${escapeHtml(`${source.label}: ${source.description}`)}"></em></button>`;
  }).join("") : '<div class="graph-unavailable">No server-returned causal path yet.</div>';
  $("causal-caption").textContent = nodes.length ? "Each hop is returned from FalkorDB; select a node to inspect its status and provenance." : "Only causal paths returned by FalkorDB are displayed.";
  const plan = ui.snapshot.current_plan; const historical = Boolean(plan?.display_only || plan?.historical || plan?.is_current === false);
  $("path-status").innerHTML = `<i></i> ${historical ? "latest applied path" : plan ? "decision path grounded" : "graph watch active"}`;
}

function renderTrackProof() {
  const snapshot = ui.snapshot; const pathLength = list(snapshot.causal_path?.nodes).length || curatedPath(snapshot).length;
  $("track-one-proof").textContent = `${pathLength}-node path → ${snapshot.current_plan ? planActions(snapshot.current_plan).length : 0} bounded actions`;
  const latestEpisode = list(snapshot.replanning?.history)[0];
  const latestEpisodeId = first(latestEpisode?.episode_id, latestEpisode?.id);
  const latestHandoffCount = snapshot.handoffs.filter((handoff) => latestEpisodeId && handoff.episode_id ? String(handoff.episode_id) === String(latestEpisodeId) : String(handoff.cycle) === String(latestEpisode?.cycle)).length;
  $("track-two-proof").textContent = `${latestHandoffCount} handoffs this cycle · ${number(snapshot.memory.episode_count)} memories`;
  const policy = list(snapshot.policies.applicable).find((item) => item.current !== false && String(item.status) !== "SUPERSEDED");
  $("track-three-proof").textContent = policy ? `${policy.id} · ${policy.owner}` : "Current policy + named owner";
}

const STORY_AGENT_KEY = {
  "AGENT-WEATHER": "weather", "AGENT-FLOW": "flow", "AGENT-GATE": "gate",
  "AGENT-TURN": "turn", "AGENT-POLICY": "policy", "AGENT-MEMORY": "memory",
};

function historyEpisodeId(item) { return first(item?.episode_id, item?.id, item?.plan_id, item?.cycle); }

function plainTrigger(trigger) {
  const value = String(trigger || "PERIODIC_CHECKPOINT").toUpperCase();
  if (value.startsWith("EVENT:")) return "Operational event";
  if (value.includes("POLICY")) return "Policy update";
  if (value.includes("MANUAL")) return "Requested check";
  return "Scheduled check";
}

function deriveDecisionStory(snapshot, historyItem = null) {
  const item = historyItem || list(snapshot.replanning?.history)[0] || null;
  if (!item) return null;
  const episodeId = String(historyEpisodeId(item));
  const currentPlan = snapshot.current_plan && String(snapshot.current_plan.episode_id || "") === episodeId ? snapshot.current_plan : null;
  const handoffs = snapshot.handoffs.filter((handoff) => handoff.episode_id ? String(handoff.episode_id) === episodeId : number(handoff.cycle) === number(item.cycle));
  const status = String(first(item.status, currentPlan?.status, "NO_ACTION_REQUIRED")).toUpperCase();
  const noAction = !currentPlan || status.includes("NO_ACTION") || status.includes("NO_CHANGE");
  const selectedId = String(first(currentPlan?.candidate_id, ""));
  const allowlisted = list(currentPlan?.model_candidate_set).map((candidate) => String(candidate.id));
  const eventIds = list(currentPlan?.disruption_context?.event_ids);
  const event = snapshot.events.find((candidate) => eventIds.includes(candidate.id)) || activeEvent(snapshot) || {};
  const checks = list(currentPlan?.safety_validation?.checks);
  const baseline = number(first(currentPlan?.baseline_delay, snapshot.metrics?.baseline_delay_minutes, snapshot.metrics?.active_delay_minutes));
  const projected = number(first(currentPlan?.projected_delay, snapshot.metrics?.projected_delay_minutes), baseline);
  const replayMinute = number(first(event.at_minute, snapshot.clock.minute));
  return {
    ok: true, episode_id: episodeId, cycle: number(item.cycle), status,
    result_kind: noAction ? "NO_ACTION" : status.includes("APPLIED") || String(currentPlan?.status).toUpperCase() === "APPLIED" ? "APPLIED" : "PROPOSED",
    replay_minute: replayMinute, replay_time: formatClock(replayMinute), graph_version: number(first(currentPlan?.based_on_version, snapshot.state_version)),
    headline: noAction ? `Checked at ${formatClock(replayMinute)}; no operational change was justified.` : `${humanize(currentPlan.strategy)} ${String(currentPlan.status).toUpperCase() === "APPLIED" ? "was applied after every guard passed" : "is ready for approval"}.`,
    summary: first(item.summary, currentPlan?.summary),
    trigger: { code: item.trigger, kind: plainTrigger(item.trigger), title: first(event.description, event.summary, humanize(event.type), plainTrigger(item.trigger)), detail: first(event.summary, item.summary, "Connected state opened a decision review."), source_kind: first(event.source_kind, "DERIVED"), event_ids: eventIds },
    why_now: { explanation: first(event.summary, item.summary, "The graph changed enough to open a new review.") },
    stages: [
      ["detected", "Detected", "complete"], ["read", "6 agents read", handoffs.length ? "complete" : "active"], ["compared", "Options compared", noAction || currentPlan ? "complete" : "pending"],
      ["model", "Qwen chose", noAction ? "skipped" : currentPlan ? "complete" : "pending"], ["safe", "Safety checked", noAction || checks.length ? "complete" : "pending"],
      ["applied", "Applied", noAction ? "skipped" : String(currentPlan?.status).toUpperCase() === "APPLIED" ? "complete" : "active"], ["watching", "Watching", noAction || currentPlan ? "active" : "pending"],
    ].map(([id, label, stageStatus]) => ({ id, label, status: stageStatus })),
    findings: handoffs.map((handoff) => ({ agent_id: handoff.from_agent, agent: agentLabel(handoff.from_agent), summary: handoff.summary, based_on_version: first(handoff.input_state_version, handoff.state_version, currentPlan?.based_on_version), evidence_ids: list(handoff.evidence_ids), handoff_id: handoff.id })),
    candidates: list(currentPlan?.safe_candidate_set).map((candidate) => ({ ...candidate, label: humanize(candidate.strategy), selected: String(candidate.id) === selectedId, shown_to_model: allowlisted.includes(String(candidate.id)), reason: String(candidate.id) === selectedId ? "Selected from the server-approved set." : allowlisted.includes(String(candidate.id)) ? "Approved finalist; not selected." : "Safe, but outside the model context window." })),
    selection: { source: currentPlan?.selected_by, selected_candidate_id: selectedId || null, allowlisted_candidate_ids: allowlisted, summary: currentPlan?.summary, explanation: noAction ? "No model call was needed because no material intervention passed the decision guard." : allowlisted.length === 1 ? "Qwen confirmed the only safe option for this situation. It still could not execute it." : `Qwen compared ${allowlisted.length} safe options and chose one. It could not invent an option or execute it.` },
    guards: checks.map((check) => ({ id: check.id, label: humanize(check.id), passed: check.passed === true, detail: check.detail, evidence_ids: list(check.evidence_ids) })),
    no_action_reason: noAction ? first(item.summary, "The scan found no material change that justified another intervention.") : null,
    outcome: { available: Boolean(currentPlan), applied: String(currentPlan?.status).toUpperCase() === "APPLIED", baseline_delay_minutes: baseline, projected_delay_minutes: projected, recovered_minutes: number(currentPlan?.recovered_minutes, Math.max(0, baseline - projected)), protected_flight_ids: list(currentPlan?.actions).filter((action) => String(action.type) === "PROTECT_ROTATION").map((action) => first(action.affected_flight_id, action.target_id)).filter(Boolean), qualifier: "Projected under the bounded replay assumptions; not measured real-world impact." },
    next_checkpoint: { minute: replayMinute + number(snapshot.replanning?.interval_minutes, 5), label: formatClock(replayMinute + number(snapshot.replanning?.interval_minutes, 5)), detail: `Next scheduled graph scan at ${formatClock(replayMinute + number(snapshot.replanning?.interval_minutes, 5))}, or immediately if connected state changes.` },
    technical: { plan_id: currentPlan?.id, evidence_ids: [...new Set(handoffs.flatMap((handoff) => list(handoff.evidence_ids)))] },
  };
}

function candidateLabel(candidate) { return first(candidate.label, humanize(candidate.strategy), "Recovery option"); }
function candidateExplanation(candidate) {
  const labels = {
    TURNAROUND_RECOVERY: "Resequence connected turnaround tasks.",
    FLOW_BALANCE: "Adjust departure flow for the rotation.",
    GATE_SWAP: "Move the rotation to a compatible gate.",
  };
  return first(labels[String(candidate?.strategy || "").toUpperCase()], "Bounded recovery strategy.");
}

function guardLabel(guard) {
  const labels = {
    CURRENT_GRAPH_STATE: "Same graph version", CURRENT_POLICY_AUTHORITY: "Current authority", CURRENT_KNOWLEDGE: "Fresh procedure",
    TARGETS_EXIST: "Required links exist", EXECUTION_FREEZE: "Freeze window clear", MINIMUM_BENEFIT: "Benefit threshold met", CHURN_GUARD: "Churn guard clear",
  };
  return first(guard.label, labels[String(guard.id)], humanize(guard.id));
}

const STORY_SECTION_BY_PHASE = {
  trigger: "why-now", findings: "agent-findings", options: "candidate-options",
  choice: "model-choice", guards: "safety-checks", outcome: "decision-outcome", next: "next-check",
};

function setStoryPhase(phase = "trigger") {
  const nextPhase = STORY_SECTION_BY_PHASE[phase] ? phase : "trigger";
  ui.activeStoryPhase = nextPhase;
  Object.entries(STORY_SECTION_BY_PHASE).forEach(([key, id]) => {
    const section = $(id); if (!section) return;
    const selected = key === nextPhase;
    section.classList.toggle("story-active", selected);
    section.setAttribute("aria-hidden", String(!selected));
  });
  document.querySelectorAll("#stage-rail > [data-phase]").forEach((step) => {
    const selected = step.dataset.phase === nextPhase;
    step.classList.toggle("viewing", selected);
    step.setAttribute("aria-current", selected ? "step" : "false");
  });
}

function renderDecisionStory(story = ui.decisionStory) {
  if (!story) {
    ui.storyPhaseEpisode = null; ui.activeStoryPhase = "trigger";
    $("story-headline").textContent = "GroundGraph is watching the connected operation";
    $("story-subtitle").textContent = "No intervention is needed yet.";
    $("story-trigger-title").textContent = "Waiting for a material graph change";
    $("story-trigger-time").textContent = formatClock(ui.snapshot?.clock?.minute || 0);
    $("story-trigger-copy").textContent = "Scheduled and event-driven checks continue in the background.";
    $("findings-count").textContent = "0 / 6";
    $("story-candidates").innerHTML = '<p class="empty-copy">No recovery comparison is needed yet.</p>';
    $("options-count").textContent = "0 options";
    $("story-choice-title").textContent = "No model choice yet";
    $("story-selector").textContent = "WAITING";
    $("story-guards").innerHTML = ""; $("guards-count").textContent = "0 / 7";
    $("story-result-title").textContent = "No action applied"; $("story-result-status").textContent = "WATCHING";
    $("story-outcome").innerHTML = '<span><small>Downstream delay</small><strong>—</strong></span><span><small>Gate waiting</small><strong>—</strong></span><span><small>Protected flights</small><strong>—</strong></span>';
    document.querySelectorAll("#stage-rail > span").forEach((node, index) => { node.classList.toggle("active", index === 0); node.classList.remove("complete"); });
    setStoryPhase("trigger");
    return;
  }
  const storyEpisode = String(first(story.episode_id, `cycle-${number(story.cycle)}`));
  if (ui.storyPhaseEpisode !== storyEpisode) { ui.storyPhaseEpisode = storyEpisode; ui.activeStoryPhase = "trigger"; }
  const noAction = String(story.result_kind).toUpperCase() === "NO_ACTION";
  const applied = String(story.result_kind).toUpperCase() === "APPLIED";
  $("cycle-number").textContent = String(number(story.cycle)).padStart(2, "0");
  $("story-headline").textContent = first(story.headline, story.summary, "Decision review complete");
  $("story-subtitle").textContent = noAction ? first(story.no_action_reason, story.summary, "No operational change was justified.") : `Graph v${number(story.graph_version)} · ${applied ? "applied in replay" : "awaiting operator approval"}`;
  $("story-trigger-title").textContent = first(story.trigger?.title, story.trigger?.kind, "Connected state changed");
  $("story-trigger-time").textContent = first(story.replay_time, formatClock(story.replay_minute));
  $("story-trigger-copy").textContent = first(story.why_now?.explanation, story.trigger?.detail, story.summary, "This change opened a new decision review.");

  const findings = list(story.findings); $("findings-count").textContent = `${findings.length} / 6`; $("findings-title").textContent = findings.length ? "All six agents finished" : "No specialist findings were returned";
  Object.entries(STORY_AGENT_KEY).forEach(([agentId, key]) => {
    const finding = findings.find((item) => String(first(item.agent_id, item.from_agent)).toUpperCase() === agentId);
    const message = $(`${key === "turn" ? "turn" : key}-agent-message`); if (message && finding) message.textContent = first(finding.summary, finding.recommendation, "Read complete");
    const card = document.querySelector(`[data-agent="${key}"]`); if (card) { card.classList.toggle("complete", Boolean(finding)); card.title = finding ? `${first(finding.summary, "Read complete")}\nGraph v${first(finding.based_on_version, story.graph_version)}` : "Awaiting this specialist"; }
  });

  const candidates = list(story.candidates); const shortlist = new Set(list(story.selection?.allowlisted_candidate_ids).map(String));
  $("options-title").textContent = noAction ? "No recovery option cleared the material-change guard" : "Safe strategies compared";
  $("options-count").textContent = `${candidates.length} option${candidates.length === 1 ? "" : "s"}`;
  $("story-candidates").innerHTML = candidates.length ? candidates.map((candidate) => {
    return `<article class="story-candidate"><span><b>${escapeHtml(candidateLabel(candidate))}</b><small>${escapeHtml(candidateExplanation(candidate))}</small></span><strong>${number(candidate.recovered_minutes)} min<small> recovery</small></strong><strong>${number(candidate.burden_minutes)} min<small> burden</small></strong></article>`;
  }).join("") : `<p class="empty-copy">${escapeHtml(first(story.no_action_reason, "No candidate was needed for this scan."))}</p>`;

  const selected = candidates.find((candidate) => candidate.selected === true || String(candidate.id) === String(story.selection?.selected_candidate_id));
  const selection = selectionAttribution(story.selection?.source);
  $("story-choice-title").textContent = noAction ? "Model call skipped — no material action was needed" : selected ? `${candidateLabel(selected)} selected` : "No allowlisted choice was returned";
  $("story-selector").textContent = noAction ? "SKIPPED" : selection.label;
  $("story-choice-copy").textContent = first(story.selection?.explanation, story.selection?.summary, noAction ? story.no_action_reason : "The model was restricted to the server-approved shortlist.");
  const comparedCount = list(story.selection?.allowlisted_candidate_ids).length;
  $("coordinator-message").textContent = noAction ? "Skipped — no action needed" : `${comparedCount} safe option${comparedCount === 1 ? "" : "s"} compared`;

  const guards = list(story.guards); const passed = guards.filter((guard) => guard.passed === true).length;
  $("guards-title").textContent = noAction ? "Guardrails stopped an unnecessary intervention" : guards.length && passed === guards.length ? "Every execution guard passed" : "Execution remains blocked until every guard passes";
  $("guards-count").textContent = guards.length ? `${passed} / ${guards.length}` : noAction ? "NO APPLY" : "0 / 7";
  $("story-guards").innerHTML = guards.length ? guards.map((guard) => `<article class="guard-item ${guard.passed ? "" : "failed"}" title="${escapeHtml(first(guard.detail, "Validation check"))}"><i>${guard.passed ? "✓" : "!"}</i><span><strong>${escapeHtml(guardLabel(guard))}</strong><small>${escapeHtml(first(guard.detail, guard.passed ? "Passed" : "Blocked"))}</small></span></article>`).join("") : `<p class="empty-copy">${escapeHtml(first(story.no_action_reason, "No execution validation was needed."))}</p>`;

  const outcome = obj(story.outcome); const baseline = number(outcome.baseline_delay_minutes); const projected = number(outcome.projected_delay_minutes, baseline); const protectedFlights = list(outcome.protected_flight_ids);
  $("story-result-title").textContent = noAction ? "Current operation held steady" : applied ? `${candidateLabel(selected || {})} applied in the replay` : "Validated plan is waiting for approval";
  $("story-result-status").textContent = noAction ? "NO ACTION" : applied ? "APPLIED" : "PROPOSED";
  $("story-outcome").innerHTML = `<span><small>Downstream delay</small><strong>${outcome.available ? `${baseline} → ${projected} min` : "—"}</strong></span><span><small>Projected recovery</small><strong>${outcome.available ? `${number(outcome.recovered_minutes)} min` : "—"}</strong></span><span><small>Protected flights</small><strong>${outcome.available ? protectedFlights.length : "—"}</strong></span>`;
  $("story-result-copy").textContent = noAction ? first(story.no_action_reason, story.summary, "No material change justified intervention.") : first(outcome.qualifier, "Projected under the bounded replay assumptions; not measured real-world impact.");
  $("story-next-title").textContent = noAction ? "Monitoring continues without operational churn" : applied ? "The applied plan stays under graph watch" : "The proposed plan stays under graph watch";
  $("story-next-time").textContent = first(story.next_checkpoint?.label, "+5 min");
  $("story-next-copy").textContent = first(story.next_checkpoint?.detail, "GroundGraph replans on schedule or immediately when connected state changes.");

  const stages = list(story.stages); const stageMap = { detected: "trigger", read: "findings", compared: "options", model: "choice", safe: "guards", applied: "outcome", watching: "next" };
  document.querySelectorAll("#stage-rail > span").forEach((node) => { const stage = stages.find((item) => stageMap[item.id] === node.dataset.phase); const status = String(stage?.status || "pending").toLowerCase(); node.classList.toggle("complete", status === "complete" || status === "skipped"); node.classList.toggle("active", status === "active"); node.title = stage ? `${stage.label}: ${status}` : "Pending"; });
  if (!document.querySelector("#stage-rail > span.active")) document.querySelector("#stage-rail > span:last-child")?.classList.add("active");
  setStoryPhase(ui.activeStoryPhase);

  if (ui.selectedEpisode && !ui.followLive) {
    $("incident-kicker").textContent = `INSPECTING DECISION ${String(number(story.cycle)).padStart(2, "0")}`;
    $("incident-title").textContent = first(story.trigger?.title, story.headline);
    $("incident-detail").textContent = first(story.why_now?.explanation, story.trigger?.detail, story.summary);
    $("delay-before").textContent = outcome.available ? `${baseline}m` : "—"; $("delay-after").textContent = outcome.available ? `${projected}m` : "—";
    $("delay-delta").textContent = noAction ? "no material change" : `${number(outcome.recovered_minutes)} min projected recovery`;
    $("checks-value").textContent = noAction ? "NO APPLY" : guards.length ? `${passed} / ${guards.length}` : "WAITING";
    $("checks-detail").textContent = noAction ? "intervention deliberately skipped" : passed === guards.length ? "all execution guards passed" : "execution blocked";
  }
  const affectedFlight = first(story.why_now?.outbound_flight_id, list(story.affected?.flight_ids)[0]); if (affectedFlight) ui.map?.setSelectedFlight(affectedFlight);
}

function decisionLogTime(item) {
  const episodeId = String(historyEpisodeId(item));
  if (String(ui.decisionStory?.episode_id) === episodeId && ui.decisionStory?.replay_time) return ui.decisionStory.replay_time;
  if (item.replay_minute !== undefined && item.replay_minute !== null) return formatClock(number(item.replay_minute));
  const created = item.created_at ? new Date(item.created_at) : null;
  return created && !Number.isNaN(created.getTime()) ? new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit", hour12: false }).format(created) : `Cycle ${String(number(item.cycle)).padStart(2, "0")}`;
}

function renderDecisionLog() {
  const history = list(ui.snapshot?.replanning?.history); const latest = history[0];
  if (ui.followLive && latest) ui.selectedEpisode = String(historyEpisodeId(latest));
  if (ui.selectedEpisode && !history.some((item) => String(historyEpisodeId(item)) === String(ui.selectedEpisode))) { ui.selectedEpisode = latest ? String(historyEpisodeId(latest)) : null; ui.followLive = true; ui.decisionStory = null; }
  $("decision-log").innerHTML = history.length ? history.map((item, index) => {
    const id = String(historyEpisodeId(item)); const status = String(first(item.status, "NO_ACTION_REQUIRED")).toUpperCase(); const applied = status.includes("APPLIED"); const noAction = status.includes("NO_ACTION") || status.includes("NO_CHANGE") || status.includes("MONITOR");
    const label = applied ? "Recovery applied" : noAction ? "No action — kept stable" : status.includes("PROPOSED") ? "Plan ready for approval" : humanize(status);
    const selected = String(ui.selectedEpisode) === id;
    return `<button type="button" class="decision-log-card ${applied ? "applied" : noAction ? "no-action" : "action"} ${selected ? "selected" : ""}" data-episode="${escapeHtml(id)}" data-status="${escapeHtml(status)}" aria-pressed="${selected}"><i></i><span><strong>${escapeHtml(`${decisionLogTime(item)} · ${plainTrigger(item.trigger)}`)}</strong><small>${escapeHtml(first(item.summary, "Graph state checked."))}</small><em>${escapeHtml(label)}${index === 0 ? " · latest" : ""}</em></span></button>`;
  }).join("") : '<span class="log-empty">The first scheduled check will appear here.</span>';
  const selectedItem = history.find((item) => String(historyEpisodeId(item)) === String(ui.selectedEpisode));
  if (!ui.decisionStory || String(ui.decisionStory.episode_id) !== String(ui.selectedEpisode)) ui.decisionStory = deriveDecisionStory(ui.snapshot, selectedItem);
  renderDecisionStory(ui.decisionStory);
}

async function loadDecisionStory(episodeId, { followLive = false } = {}) {
  if (!episodeId) return;
  const requestId = ++ui.storyRequest; ui.selectedEpisode = String(episodeId); ui.followLive = followLive; renderDecisionLog();
  $("story-subtitle").textContent = "Loading the episode-scoped explanation from FalkorDB…";
  try {
    const payload = await request(`/api/decision-story?episode_id=${encodeURIComponent(episodeId)}`, { timeout: 15000 });
    if (requestId !== ui.storyRequest) return;
    const story = obj(first(payload.decision_story, payload.story, payload));
    if (story.ok !== true || !story.episode_id || !story.result_kind) throw Object.assign(new Error("Episode detail was not returned"), { status: 404 });
    ui.decisionStory = story; renderDecisionLog();
    const scroll = $("decision-story"); scroll.classList.remove("trace-replay"); void scroll.offsetWidth; scroll.classList.add("trace-replay");
  } catch (error) {
    if (requestId !== ui.storyRequest) return;
    const selected = list(ui.snapshot.replanning?.history).find((item) => String(historyEpisodeId(item)) === String(episodeId));
    ui.decisionStory = deriveDecisionStory(ui.snapshot, selected); renderDecisionLog();
    if (number(error?.status) !== 404) showToast("Using the snapshot explanation while episode detail reconnects");
  }
}

function renderAirports() {
  const menu = $("airport-menu"); if (!menu) return;
  const active = String(ui.snapshot?.scenario?.airport || "").toUpperCase();
  menu.innerHTML = ui.airports.length ? ui.airports.map((airport) => `<button type="button" class="airport-option ${String(airport.code).toUpperCase() === active ? "active" : ""}" role="option" data-airport="${escapeHtml(String(airport.code).toUpperCase())}" aria-selected="${String(airport.code).toUpperCase() === active}"><i>${escapeHtml(airport.code)}</i><span><strong>${escapeHtml(first(airport.label, airport.name, airport.code))}</strong><small>${escapeHtml(first(airport.summary, "Bounded historical replay"))}</small></span><em>${String(airport.code).toUpperCase() === active ? "ACTIVE" : "OPEN"}</em></button>`).join("") : '<span class="log-empty">Airport catalog unavailable.</span>';
  menu.hidden = !ui.airportMenuOpen;
  $("airport-switcher").setAttribute("aria-expanded", String(ui.airportMenuOpen));
}

async function loadAirports() {
  try { const payload = obj(await request("/api/airports", { timeout: 8000 })); ui.airports = list(payload.airports); renderAirports(); }
  catch { ui.airports = [{ code: ui.snapshot.scenario.airport, name: ui.snapshot.scenario.airport_name, label: ui.snapshot.scenario.replay_label, summary: ui.snapshot.scenario.description }]; renderAirports(); }
}

async function selectAirport(code) {
  if (!ui.backend || ui.busy || !code || String(code).toUpperCase() === String(ui.snapshot.scenario.airport).toUpperCase()) { ui.airportMenuOpen = false; renderAirports(); return; }
  const wasPlaying = ui.playing; setPlaying(false); ui.airportMenuOpen = false; renderAirports(); setBusy(true, `Loading ${String(code).toUpperCase()} replay`);
  try {
    const snapshot = requireHealthySnapshot(await request("/api/airports/select", { method: "POST", body: JSON.stringify({ airport: String(code).toUpperCase() }), timeout: 90000 }));
    ui.selectedEpisode = null; ui.decisionStory = null; ui.followLive = true; ui.selectedCycle = null; ui.policyDemo = null;
    renderSnapshot(snapshot); await loadAirports(); setPlaying(false); showToast(`${snapshot.scenario.airport} replay loaded from its own FalkorDB graph`);
  } catch (error) { if (!error?.status || number(error.status) >= 500) enterEngineUnavailable(error, { preserveSnapshot: true }); else showToast(first(error.message, "Could not switch airport")); }
  finally { setBusy(false); if (wasPlaying && ui.backend) setPlaying(true); }
}

function graphPathLabel(item) {
  if (typeof item === "string") return item;
  const edge = obj(item); return first(edge.label, edge.path, [edge.source, edge.type, edge.target].filter(Boolean).join(" → "), edge.id, "Graph evidence");
}

function brainCitationMarkup(citation) {
  if (typeof citation === "string") return `<details class="brain-evidence"><summary><strong>${escapeHtml(citation)}</strong><small>CITATION</small></summary><div>Evidence returned by Operations Brain.</div></details>`;
  const item = obj(citation); const title = first(item.title, item.policy_id, item.procedure_id, item.document_id, item.id, "Connected evidence");
  const owner = first(item.owner, item.owner_team, item.owner_team_id); const section = first(item.source_section, item.section); const version = first(item.version, item.policy_version);
  const excerpt = first(item.excerpt, item.source_excerpt, item.summary, "No excerpt was returned for this record.");
  const meta = [owner ? `Owner · ${owner}` : null, section ? `Section · ${section}` : null, version ? `Version · ${version}` : null].filter(Boolean);
  return `<details class="brain-evidence"><summary><strong>${escapeHtml(title)}</strong><small>${escapeHtml(shortStatus(first(item.freshness_status, item.status, "EVIDENCE")).toUpperCase())}</small></summary><div>${meta.length ? `<span>${escapeHtml(meta.join(" · "))}</span>` : ""}<q>${escapeHtml(excerpt)}</q></div></details>`;
}

function renderBrainAnswer(payload) {
  const answer = first(payload.answer, "Operations Brain found connected records but did not return a written answer.");
  const citations = list(payload.citations); const artifacts = list(payload.artifacts); const paths = list(payload.graph_path); const evidenceIds = list(payload.evidence_ids);
  const derivedEvidence = evidenceIds.length ? evidenceIds : citations.flatMap((citation) => typeof citation === "object" ? [first(citation.policy_id, citation.procedure_id, citation.document_id, citation.id)].filter(Boolean) : []);
  const artifactChips = artifacts.map((artifact) => typeof artifact === "string" ? artifact : first(artifact.id, artifact.title)).filter(Boolean);
  const proof = [...derivedEvidence, ...artifactChips, ...paths.map(graphPathLabel)].filter(Boolean);
  $("brain-answer").innerHTML = `<article class="brain-response"><header><strong>${escapeHtml(first(payload.question, "Operations Brain answer"))}</strong><em class="brain-answer-type">${escapeHtml(shortStatus(first(payload.answer_type, payload.truth_label, "GRAPH ANSWER")).toUpperCase())}</em></header><p>${escapeHtml(answer)}</p>${proof.length ? `<div class="brain-proof-row">${proof.map((item) => `<span>${escapeHtml(item)}</span>`).join("")}</div>` : ""}${citations.length ? citations.map(brainCitationMarkup).join("") : '<details class="brain-evidence"><summary><strong>No supporting citation returned</strong><small>CHECK EVIDENCE</small></summary><div>The answer is shown as returned, without inventing a source.</div></details>'}</article>`;
}

function setBrainBusy(active, label = "reading graph") {
  ui.brainBusy = active; const card = document.querySelector(".brain-card"); card?.classList.toggle("busy", active); if (active) card?.classList.remove("error");
  $("brain-status").lastChild.textContent = ` ${active ? label : "ready"}`;
  document.querySelectorAll("[data-brain-question], #brain-question, #brain-submit").forEach((control) => { control.disabled = active || !ui.backend; });
}

async function askOperationsBrain(question) {
  const value = String(question || "").trim(); if (!value || ui.brainBusy) return;
  $("brain-question").value = value; setBrainBusy(true);
  $("brain-answer").innerHTML = '<span class="brain-empty"><i>…</i><span><strong>Following the connected evidence</strong><small>Checking current versions, owners, incidents, and their graph relationships.</small></span></span>';
  let failed = false;
  try {
    const payload = obj(await request("/api/company-brain", { method: "POST", body: JSON.stringify({ question: value }), timeout: 12000 }));
    if (payload.ok !== true) throw new Error(first(payload.error, "Operations Brain could not answer from the graph."));
    renderBrainAnswer(payload);
  } catch (error) {
    failed = true;
    if (!error?.status || number(error.status) >= 500) enterEngineUnavailable(error, { preserveSnapshot: true });
    $("brain-answer").innerHTML = `<div class="brain-error">${escapeHtml(first(error.message, "Operations Brain is unavailable."))}</div>`;
  } finally {
    setBrainBusy(false);
    if (failed) { document.querySelector(".brain-card")?.classList.add("error"); $("brain-status").lastChild.textContent = " answer unavailable"; }
  }
}

async function setAutonomy(enabled) {
  if (!ui.backend || ui.busy) return;
  const toggle = $("auto-apply"); toggle.disabled = true; $("auto-apply-state").textContent = "updating server…";
  try {
    const result = requireHealthySnapshot(await request("/api/autonomy", { method: "POST", body: JSON.stringify({ enabled: Boolean(enabled) }) }));
    renderSnapshot(result, { keepVisualTime: true });
    showToast(result.replanning?.auto_apply === true ? "Guarded auto-apply enabled on the server" : "Plans will wait for operator approval");
  } catch (error) {
    toggle.checked = ui.snapshot.replanning.auto_apply === true;
    $("auto-apply-state").textContent = toggle.checked ? "ON" : "OFF";
    if (!error?.status || number(error.status) >= 500) {
      enterEngineUnavailable(error, { preserveSnapshot: true });
      showToast("Engine unavailable — replay frozen");
    } else showToast(first(error.message, "Could not update autonomy setting"));
  } finally { toggle.disabled = !ui.backend; }
}

function renderTab() {
  const labels = { plan: "Recovery plan", cycles: "Six-agent trace", evidence: "Graph and policy proof", memory: "Episodic memory" };
  ["plan", "cycles", "evidence", "memory"].forEach((name) => {
    const button = document.querySelector(`[data-tab="${name}"]`); if (button) { const active = name === ui.selectedTab; button.classList.toggle("active", active); button.setAttribute("aria-selected", String(active)); }
  });
  const drawer = $("proof-drawer");
  if (drawer) drawer.hidden = !ui.selectedTab;
  if (ui.selectedTab && $("proof-drawer-title")) $("proof-drawer-title").textContent = labels[ui.selectedTab] || "Decision detail";
  $("waiting-state").hidden = ui.selectedTab !== "plan" || Boolean(ui.snapshot.current_plan);
  $("plan-stage").hidden = ui.selectedTab !== "plan" || !ui.snapshot.current_plan;
  $("cycles-stage").hidden = ui.selectedTab !== "cycles";
  $("evidence-stage").hidden = ui.selectedTab !== "evidence";
  $("memory-stage").hidden = ui.selectedTab !== "memory";
}

function updatePrimaryAction() {
  const button = $("primary-action"); const plan = ui.snapshot.current_plan; const status = String(plan?.status || "").toUpperCase(); button.classList.toggle("applied", status === "APPLIED");
  if (!ui.backend) { button.disabled = true; $("primary-action-label").textContent = "FalkorDB required"; return; }
  if (ui.busy) { button.disabled = true; $("primary-action-label").textContent = "Replanning…"; return; }
  button.disabled = false;
  if (plan && status === "PROPOSED") $("primary-action-label").textContent = "Apply plan";
  else if (plan && status === "APPLIED") $("primary-action-label").textContent = "View latest cycle";
  else $("primary-action-label").textContent = "Run replan now";
}

function renderUnavailablePresentation() {
  if (ui.source === "api-frozen") return;
  $("scenario-label").textContent = "Operational graph not loaded";
  $("window-label").textContent = "Replay locked";
  $("weather-primary").textContent = "No graph snapshot available";
  $("weather-secondary").textContent = "FalkorDB connection required";
  $("incident-kicker").textContent = "SYSTEM STATUS";
  $("incident-provenance").className = "provenance-tag"; $("incident-provenance").textContent = "SYSTEM"; $("incident-provenance").title = "Runtime availability status";
  $("incident-title").textContent = "Planning engine unavailable";
  $("incident-detail").textContent = "No operational events are being simulated in this browser.";
  const severity = document.querySelector(".severity-symbol"); if (severity) severity.textContent = "!";
  $("event-time").textContent = "--:--"; $("event-symbol").textContent = "!";
  $("event-title").textContent = "FalkorDB is required";
  $("event-detail").textContent = "Start the GroundGraph engine and reload to begin the replay.";
  $("event-provenance").className = "provenance-tag"; $("event-provenance").textContent = "SYSTEM"; $("event-provenance").title = "Runtime availability status";
  $("replan-state").textContent = "Engine unavailable";
  $("replan-next").textContent = "No agents, scans, or decisions are running";
  Object.values(AGENT_KEYS).forEach((key) => {
    const target = $(`${key === "turn" ? "turn" : key}-agent-message`) || (key === "coordinator" ? $("coordinator-message") : null);
    if (target) target.textContent = "Offline · graph required";
    const card = document.querySelector(`[data-agent="${key}"]`); card?.classList.remove("active", "complete", "risk");
  });
  $("safety-agent-message").textContent = "Offline · no state to validate";
  document.querySelector('[data-agent="safety"]')?.classList.remove("active", "complete", "risk");
  $("handoff-route").textContent = "FalkorDB connection required";
  $("handoff-message").textContent = "No client-side handoffs or decisions are generated.";
  $("graph-version").textContent = "offline";
  $("waiting-kicker").textContent = "READ-ONLY SAFETY STATE";
  $("waiting-title").textContent = "No operational state loaded";
  $("waiting-copy").textContent = "GroundGraph does not run a browser fallback. Connect the API and FalkorDB to start the six specialists, coordinator, and validator.";
  $("causal-title").textContent = "Operational graph unavailable";
  $("causal-path").innerHTML = '<div class="graph-unavailable">No causal path is generated without FalkorDB.</div>';
  $("causal-caption").textContent = "Only paths returned by the GroundGraph API are rendered.";
  $("path-status").innerHTML = "<i></i> engine required";
  $("track-one-proof").textContent = "Awaiting genuine graph path";
  $("track-two-proof").textContent = "Awaiting persisted handoffs";
  $("track-three-proof").textContent = "Awaiting current policy";
  $("checks-value").textContent = "OFFLINE"; $("checks-detail").textContent = "no state validated";
}

function syncEngineAvailability() {
  const available = ui.backend;
  document.body.classList.toggle("engine-offline", !available);
  ui.map?.setEngineAvailable?.(available);
  ["timeline", "jump-button", "reset-button", "auto-apply"].forEach((id) => { const control = $(id); if (control) control.disabled = !available || ui.busy; });
  if ($("play-button")) $("play-button").disabled = !available;
  document.querySelectorAll("[data-speed], .publish-policy-update").forEach((control) => { control.disabled = !available || ui.busy; });
  document.querySelectorAll("[data-brain-question], #brain-question, #brain-submit").forEach((control) => { control.disabled = !available || ui.brainBusy; });
  const panel = $("engine-unavailable");
  if (panel) {
    panel.hidden = available;
    const connecting = ui.source === "connecting";
    const frozen = ui.source === "api-frozen";
    $("engine-unavailable-title").textContent = connecting ? "Connecting to GroundGraph" : frozen ? "Engine connection lost" : "Planning engine unavailable";
    $("engine-unavailable-copy").textContent = connecting
      ? "Verifying the API, FalkorDB graph, and planning runtime before enabling controls."
      : frozen
      ? "The last genuine server snapshot is frozen in read-only mode. Restore FalkorDB; GroundGraph will reload graph state before re-enabling controls."
      : "GroundGraph requires the API and FalkorDB to load state, run agents, replan, or apply decisions.";
  }
  if (!available) renderUnavailablePresentation();
  if (!available) { $("play-icon").textContent = "!"; $("play-label").textContent = "Unavailable"; }
}

function enterEngineUnavailable(error, { preserveSnapshot = false } = {}) {
  ui.playing = false; ui.busy = false; ui.backend = false; ui.health = {};
  ui.unavailableReason = first(error?.message, "FalkorDB is required to run GroundGraph.");
  const preserveGenuine = preserveSnapshot && ["api", "api-frozen"].includes(ui.source) && ui.snapshot?.ok === true;
  ui.source = preserveGenuine ? "api-frozen" : "unavailable";
  renderSnapshot(preserveGenuine ? ui.snapshot : UNAVAILABLE_SNAPSHOT, { keepVisualTime: preserveGenuine });
}

function renderSnapshot(snapshot, { keepVisualTime = false } = {}) {
  ui.snapshot = normalizeSnapshot(snapshot); ui.targetMinute = ui.snapshot.clock.minute; if (!keepVisualTime) ui.visualMinute = ui.targetMinute;
  renderRuntime(); renderScenario(); renderWeather(); renderIncident(); renderMetrics(); renderAgents(); renderReplanning(); renderPlan(); renderEvidenceAndMemory(); renderCausalPath(); renderTrackProof(); renderDecisionLog(); renderAirports(); renderTab(); updatePrimaryAction();
  ui.map?.setSnapshot(ui.snapshot); ui.map?.setTime(ui.visualMinute);
  $("footer-status").textContent = ui.backend
    ? `GroundGraph · cycle ${ui.snapshot.clock.cycle} · graph v${ui.snapshot.state_version} · FalkorDB-backed replay`
    : `GroundGraph · read-only · FalkorDB required`;
  syncEngineAvailability();
  const current = ui.snapshot.current_plan; const status = String(current?.status || "").toUpperCase();
  const historical = Boolean(current?.display_only || current?.historical || current?.is_current === false);
  if (!historical && status === "PROPOSED" && ui.snapshot.replanning.auto_apply !== true) setPlaying(false);
  const latestEpisode = historyEpisodeId(list(ui.snapshot.replanning?.history)[0]);
  if (latestEpisode && ui.followLive) {
    queueMicrotask(() => loadDecisionStory(latestEpisode, { followLive: true }));
  }
}

function setBusy(active, label = "Specialists are reading FalkorDB") {
  ui.busy = active; $("replan-radar").classList.toggle("running", active); if (active) { $("replan-state").textContent = label; $("replan-next").textContent = "Parallel findings → coordinator → safety validator"; }
  if (!active && ui.snapshot) renderReplanning();
  renderAgents(); updatePrimaryAction(); syncEngineAvailability();
}

async function mutation(path, body) {
  if (ui.busy) return null;
  if (!ui.backend) { enterEngineUnavailable(new Error("FalkorDB is required for operational actions.")); return null; }
  ui.followLive = true; setBusy(true);
  try {
    const result = requireHealthySnapshot(await request(path, { method: "POST", body: JSON.stringify(body || {}), timeout: 90000 }));
    renderSnapshot(result, { keepVisualTime: path.includes("tick") }); return result;
  } catch (error) {
    if (number(error?.status) >= 400 && number(error?.status) < 500) {
      showToast(first(error.message, "The current graph state rejected that action"));
    } else {
      enterEngineUnavailable(error, { preserveSnapshot: true });
      showToast("Engine unavailable — replay frozen");
    }
    return null;
  } finally {
    setBusy(false);
  }
}

async function advance(minutes) {
  if (!ui.backend || !ui.playing || ui.busy || ui.snapshot.clock.minute >= ui.snapshot.scenario.end_minute) { if (ui.snapshot?.clock?.minute >= ui.snapshot?.scenario?.end_minute) setPlaying(false); return; }
  await mutation("/api/replan/tick", { minutes: Math.max(1, Math.min(30, Math.round(minutes))) });
}

async function verifyRuntimeAvailability() {
  if (ui.busy || ui.brainBusy || ui.healthChecking) return;
  ui.healthChecking = true;
  try {
    const health = requireHealthyRuntime(await request("/api/health", { timeout: 5000 }));
    ui.healthFailures = 0;
    if (!ui.backend) {
      const snapshot = requireHealthySnapshot(await request("/api/snapshot", { timeout: 15000 }));
      ui.health = health; ui.backend = true; ui.source = "api"; ui.unavailableReason = "";
      renderSnapshot(snapshot);
      setPlaying(false);
      showToast("FalkorDB restored — live replay reconnected");
    } else {
      ui.health = health;
      renderRuntime();
    }
  } catch (error) {
    ui.healthFailures += 1;
    const explicitGraphOutage = number(error?.status) === 503 && error?.payload?.code === "GRAPH_UNAVAILABLE";
    if (ui.backend && (explicitGraphOutage || ui.healthFailures >= 2)) {
      enterEngineUnavailable(error, { preserveSnapshot: true });
      showToast("Engine connection lost — replay frozen");
    }
  } finally { ui.healthChecking = false; }
}

async function runReplan(trigger = "MANUAL_UI") {
  const result = await mutation("/api/analyze", { trigger });
  if (result) showToast(result.current_plan ? "Graph-safe plan returned" : "Cycle complete: no material change");
}
async function applyPlan() {
  const plan = ui.snapshot.current_plan; if (!plan || String(plan.status).toUpperCase() !== "PROPOSED") return;
  const result = await mutation("/api/apply", { plan_id: plan.id, state_version: ui.snapshot.state_version });
  if (result) { ui.map?.setPlanApplied(true); showToast("Validated recovery applied to the replay"); }
}

async function publishPolicyUpdate(policyId) {
  if (ui.busy) return;
  if (!ui.backend) { enterEngineUnavailable(new Error("FalkorDB is required to publish a policy update.")); return; }
  const policy = currentGatePolicy();
  if (!policy || String(policy.id) !== String(policyId)) { showToast("The current gate policy changed; refresh the evidence view"); return; }
  const nextVersion = incrementPolicyVersion(policy.version);
  const newId = String(policy.id).replace(/\d+(?:\.\d+)*$/, nextVersion);
  const oldState = ui.snapshot.state_version;
  const oldCycle = number(ui.snapshot.replanning.cycle, ui.snapshot.clock.cycle);
  const wasPlaying = ui.playing; setPlaying(false);
  const button = document.querySelector(".publish-policy-update"); if (button) { button.disabled = true; button.textContent = "Publishing…"; }
  const payload = {
    policy_id: policy.id,
    replacement: {
      id: newId,
      title: policy.title,
      version: nextVersion,
      owner: policy.owner,
      source: policy.source,
      action_types: list(policy.action_types).length ? policy.action_types : ["REASSIGN_GATE"],
      rule: `${first(policy.rule, "A compatible open gate may be reassigned.")} A fresh graph-state validation is required after this synthetic publication.`,
      source_kind: "MODELED",
    },
  };
  setBusy(true, "Publishing a synthetic policy update");
  try {
    const result = requireHealthySnapshot(await request("/api/policies/supersede", { method: "POST", body: JSON.stringify(payload) }));
    const normalized = normalizeSnapshot(result);
    ui.policyDemo = { oldId: policy.id, newId, oldState, newState: normalized.state_version, cycle: number(normalized.replanning.cycle, oldCycle + 1) };
    renderSnapshot(normalized);
    ui.selectedTab = "evidence"; renderTab();
    showToast(`Published ${newId}; graph v${oldState} → v${normalized.state_version} and replanned`);
  } catch (error) { enterEngineUnavailable(error, { preserveSnapshot: true }); showToast("Engine unavailable — policy update not published"); }
  finally { setBusy(false); if (wasPlaying && ui.backend) setPlaying(true); }
}
async function resetReplay() {
  ui.policyDemo = null; ui.selectedCycle = null; ui.selectedEpisode = null; ui.decisionStory = null; ui.followLive = true; setPlaying(false); setBusy(true, "Resetting replay state");
  if (!ui.backend) { setBusy(false); enterEngineUnavailable(new Error("FalkorDB is required to reset the replay.")); return; }
  try { const snapshot = requireHealthySnapshot(await request("/api/reset", { method: "POST", body: "{}" })); renderSnapshot(snapshot); setPlaying(false); showToast("Replay restarted and paused at 08:00"); }
  catch (error) { enterEngineUnavailable(error, { preserveSnapshot: true }); showToast("Engine unavailable — replay frozen"); } finally { setBusy(false); }
}

function setPlaying(active) {
  ui.playing = Boolean(active) && ui.backend && ui.snapshot.clock.minute < ui.snapshot.scenario.end_minute;
  if (!ui.backend) {
    $("play-icon").textContent = "!"; $("play-label").textContent = "Unavailable";
    $("play-button").setAttribute("aria-pressed", "false");
    return;
  }
  $("play-icon").textContent = ui.playing ? "Ⅱ" : "▶"; $("play-label").textContent = ui.playing ? "Pause" : "Play";
  $("play-button").setAttribute("aria-pressed", String(ui.playing));
}

async function seek(target) {
  if (!ui.backend) { enterEngineUnavailable(new Error("FalkorDB is required to move the replay clock.")); return; }
  const minute = Math.max(ui.snapshot.scenario.start_minute, Math.min(ui.snapshot.scenario.end_minute, Math.round(number(target)))); setPlaying(false);
  if (minute < ui.snapshot.clock.minute) {
    try {
      const reset = requireHealthySnapshot(await request("/api/reset", { method: "POST", body: "{}" })); renderSnapshot(reset);
    } catch (error) { enterEngineUnavailable(error, { preserveSnapshot: true }); showToast("Engine unavailable — replay frozen"); return; }
  }
  const delta = minute - ui.snapshot.clock.minute;
  if (delta > 0) await mutation("/api/replan/tick", { to_minute: minute });
}

function animationFrame(timestamp) {
  const elapsed = ui.lastFrameAt ? Math.min(0.1, (timestamp - ui.lastFrameAt) / 1000) : 0; ui.lastFrameAt = timestamp;
  if (ui.backend || ui.source === "api-frozen") {
    const gap = ui.targetMinute - ui.visualMinute; ui.visualMinute += gap * Math.min(1, elapsed * 5.2); if (Math.abs(gap) < 0.02) ui.visualMinute = ui.targetMinute;
    ui.map?.setTime(ui.visualMinute); $("replay-clock").textContent = formatClock(ui.visualMinute); if (!ui.scrubbing) $("timeline").value = String(ui.visualMinute);
  } else {
    $("replay-clock").textContent = "--:--";
  }
  ui.frame = requestAnimationFrame(animationFrame);
}

function bindInteractions() {
  $("play-button").addEventListener("click", () => setPlaying(!ui.playing));
  document.querySelectorAll("[data-speed]").forEach((button) => button.addEventListener("click", () => {
    ui.speed = number(button.dataset.speed, 4); document.querySelectorAll("[data-speed]").forEach((item) => { const active = item === button; item.classList.toggle("active", active); item.setAttribute("aria-pressed", String(active)); }); showToast(`Replay speed ${ui.speed}×`);
  }));
  $("timeline").addEventListener("pointerdown", () => { ui.scrubbing = true; });
  $("timeline").addEventListener("input", (event) => { ui.visualMinute = number(event.target.value); $("replay-clock").textContent = formatClock(ui.visualMinute); ui.map?.setTime(ui.visualMinute); });
  $("timeline").addEventListener("change", async (event) => { ui.scrubbing = false; await seek(event.target.value); });
  $("jump-button").addEventListener("click", async () => { const bag = ui.snapshot.events.find((event) => String(event.type).includes("BAG")); await seek(number(bag?.at_minute, 36)); });
  $("reset-button").addEventListener("click", resetReplay);
  $("primary-action").addEventListener("click", () => {
    const plan = ui.snapshot.current_plan; const status = String(plan?.status || "").toUpperCase();
    if (plan && status === "PROPOSED") applyPlan(); else if (plan && status === "APPLIED") { ui.selectedTab = "cycles"; renderTab(); } else runReplan();
  });
  $("auto-apply").addEventListener("change", (event) => setAutonomy(event.target.checked));
  document.querySelectorAll("[data-tab]").forEach((button) => button.addEventListener("click", () => { ui.selectedTab = button.dataset.tab; renderTab(); }));
  $("proof-close").addEventListener("click", () => { ui.selectedTab = null; renderTab(); });
  $("airport-switcher").addEventListener("click", (event) => { event.stopPropagation(); ui.airportMenuOpen = !ui.airportMenuOpen; renderAirports(); });
  $("airport-menu").addEventListener("click", (event) => { const option = event.target.closest("[data-airport]"); if (option) selectAirport(option.dataset.airport); });
  $("decision-log").addEventListener("click", (event) => {
    const row = event.target.closest("[data-episode]"); if (!row) return;
    const latest = historyEpisodeId(list(ui.snapshot.replanning?.history)[0]);
    loadDecisionStory(row.dataset.episode, { followLive: String(row.dataset.episode) === String(latest) });
  });
  $("stage-rail").addEventListener("click", (event) => {
    const step = event.target.closest("[data-phase]"); if (!step) return;
    setStoryPhase(step.dataset.phase);
  });
  $("causal-path").addEventListener("click", (event) => { const node = event.target.closest(".causal-node"); if (!node) return; document.querySelectorAll(".causal-node").forEach((item) => item.classList.toggle("active", item === node)); $("causal-caption").textContent = node.dataset.detail; ui.map?.setSelectedFlight(node.dataset.node); });
  $("cycles-stage").addEventListener("click", (event) => { const row = event.target.closest(".cycle-row"); if (!row) return; ui.selectedCycle = first(row.dataset.episode, row.dataset.cycle); renderReplanning(); const item = list(ui.snapshot.replanning.history).find((entry) => String(first(entry.episode_id, entry.id, entry.plan_id, entry.cycle)) === String(ui.selectedCycle)); if (item) { $("causal-caption").textContent = `Cycle ${item.cycle} · ${shortStatus(item.status)} · ${item.summary}`; loadDecisionStory(historyEpisodeId(item), { followLive: false }); } });
  $("evidence-stage").addEventListener("click", (event) => {
    const publish = event.target.closest(".publish-policy-update");
    if (publish) { publishPolicyUpdate(publish.dataset.policyId); return; }
    if (event.target.closest(".view-policy-cycle")) { ui.selectedTab = "cycles"; renderTab(); }
  });
  $("action-list").addEventListener("click", (event) => { const row = event.target.closest(".action-item"); if (row?.dataset.flight) { ui.map?.setSelectedFlight(row.dataset.flight); showToast("Highlighted the action target and its graph path"); } });
  document.querySelectorAll("[data-brain-question]").forEach((button) => button.addEventListener("click", () => askOperationsBrain(button.dataset.brainQuestion)));
  $("brain-form").addEventListener("submit", (event) => { event.preventDefault(); askOperationsBrain($("brain-question").value); });
  document.addEventListener("click", (event) => { const picker = $("airport-selector") || document.querySelector(".airport-picker"); if (ui.airportMenuOpen && (!picker || !picker.contains(event.target))) { ui.airportMenuOpen = false; renderAirports(); } });
  document.addEventListener("keydown", (event) => { if (event.key !== "Escape") return; ui.airportMenuOpen = false; ui.selectedTab = null; renderAirports(); renderTab(); });
}

async function boot() {
  bindInteractions(); ui.map = window.GroundGraphAirport.create("#airport-canvas", { onCounts: (counts) => { $("airborne-count").textContent = counts.airborne; $("taxi-count").textContent = counts.taxiing; $("gate-count").textContent = counts.atGate; $("risk-count").textContent = counts.risk; $("protected-count").textContent = counts.protected; } });
  ui.source = "connecting"; renderSnapshot(UNAVAILABLE_SNAPSHOT);
  try {
    const [snapshotResult, healthResult] = await Promise.all([request("/api/snapshot", { timeout: 3500 }), request("/api/health", { timeout: 3500 })]);
    const snapshot = requireHealthySnapshot(snapshotResult); ui.health = requireHealthyRuntime(healthResult); ui.backend = true; ui.source = "api"; ui.unavailableReason = "";
    renderSnapshot(snapshot); await loadAirports(); setPlaying(false); syncEngineAvailability();
  } catch (error) { enterEngineUnavailable(error); }
  ui.tickTimer = setInterval(() => advance(ui.speed), 1000);
  ui.healthTimer = setInterval(verifyRuntimeAvailability, 5000);
  ui.frame = requestAnimationFrame(animationFrame);
}

window.addEventListener("beforeunload", () => { clearInterval(ui.tickTimer); clearInterval(ui.healthTimer); cancelAnimationFrame(ui.frame); ui.map?.destroy(); });
boot();
