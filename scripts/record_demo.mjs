#!/usr/bin/env node

import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(scriptDir, "..");
const require = createRequire(import.meta.url);

const SCENE_ORDER = ["hook", "why-now", "findings", "options", "apply", "restraint", "memory-knowledge", "airports", "close"];
const UI = {
  airportSelector: "#airport-selector, #airport-switcher",
  airportButton: "#airport-switcher, #airport-selector button",
  airportMap: "#airport-map, #airport-view",
  stageRail: "#stage-rail, .stage-rail, .story-rail",
  story: "#decision-story",
  whyNow: "#why-now, .story-trigger",
  findings: "#agent-findings, .story-findings",
  options: "#candidate-options, .story-options",
  modelChoice: "#model-choice, .story-choice",
  safety: "#safety-checks, .story-guards",
  outcome: "#decision-outcome, .story-result",
  decisionLog: "#decision-log",
  primaryAction: "#primary-action",
  jump: "#jump-button",
  play: "#play-button",
};

function parseArgs(argv) {
  const options = {
    mode: "dry-run",
    url: "http://127.0.0.1:5050/",
    voice: "Daniel",
    rate: "185",
    secondaryAirport: "ATL",
    output: path.join(repoRoot, "demo", "groundgraph-demo-candidate.mp4"),
    screenshot: path.join(repoRoot, ".artifacts", "demo-pipeline", "dry-run.png"),
    keepWork: false,
    force: false,
    allowMutations: false,
  };
  for (const argument of argv) {
    if (argument === "--dry-run") options.mode = "dry-run";
    else if (argument === "--record") options.mode = "record";
    else if (argument === "--allow-mutations") options.allowMutations = true;
    else if (argument === "--keep-work") options.keepWork = true;
    else if (argument === "--force") options.force = true;
    else if (argument.startsWith("--url=")) options.url = argument.slice("--url=".length);
    else if (argument.startsWith("--voice=")) options.voice = argument.slice("--voice=".length);
    else if (argument.startsWith("--rate=")) options.rate = argument.slice("--rate=".length);
    else if (argument.startsWith("--secondary-airport=")) options.secondaryAirport = argument.slice("--secondary-airport=".length).toUpperCase();
    else if (argument.startsWith("--output=")) options.output = path.resolve(repoRoot, argument.slice("--output=".length));
    else if (argument.startsWith("--screenshot=")) options.screenshot = path.resolve(repoRoot, argument.slice("--screenshot=".length));
    else if (argument === "--help" || argument === "-h") options.mode = "help";
    else throw new Error(`Unknown argument: ${argument}`);
  }
  return options;
}

function showHelp() {
  console.log(`GroundGraph judge-demo recorder

Read-only contract and rendering check:
  ./scripts/record_demo.sh --dry-run

Record a review candidate (mutates replay state):
  ./scripts/record_demo.sh --record --allow-mutations --force

Options:
  --url=http://127.0.0.1:5050/
  --output=demo/groundgraph-demo-candidate.mp4
  --voice=Daniel --rate=185
  --secondary-airport=ATL
  --screenshot=.artifacts/demo-pipeline/dry-run.png
  --keep-work   Retain captured JPEG frames, subtitles, and intermediate audio.
  --force       Replace an existing candidate output.

The recorder refuses to write demo/groundgraph-demo.mp4. It also refuses to
record unless the UI, FalkorDB state, Qwen allowlist, seven guards, real
NO_ACTION episode, and distinct secondary-airport graph all match the story.`);
}

function assert(condition, message) {
  if (!condition) throw new Error(`Demo assertion failed: ${message}`);
}
function list(value) { return Array.isArray(value) ? value : []; }
function words(value) { return String(value || "").match(/\b[\p{L}\p{N}'’-]+\b/gu) || []; }

function findPlaywright() {
  const candidates = [
    process.env.GROUNDGRAPH_PLAYWRIGHT,
    "playwright",
    path.join(os.homedir(), ".cache", "codex-runtimes", "codex-primary-runtime", "dependencies", "node", "node_modules", "playwright"),
  ].filter(Boolean);
  for (const candidate of candidates) {
    try { return require(candidate); } catch { /* Try the next installation. */ }
  }
  throw new Error("Playwright was not found. Install playwright or set GROUNDGRAPH_PLAYWRIGHT.");
}

function chromePath() {
  const candidates = [
    process.env.GROUNDGRAPH_CHROME,
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
  ].filter(Boolean);
  const executable = candidates.find((candidate) => fs.existsSync(candidate));
  if (!executable) throw new Error("Google Chrome or Chromium was not found. Set GROUNDGRAPH_CHROME.");
  return executable;
}

function command(name, args, options = {}) {
  const result = spawnSync(name, args, { encoding: "utf8", stdio: options.capture ? "pipe" : "inherit", ...options });
  if (result.status !== 0) {
    const detail = [result.stdout, result.stderr].filter(Boolean).join("\n");
    throw new Error(`${name} failed with status ${result.status}${detail ? `:\n${detail}` : ""}`);
  }
  return options.capture ? String(result.stdout || "").trim() : "";
}
function requireCommand(name) { command("/usr/bin/env", ["sh", "-c", `command -v ${name}`], { capture: true }); }

async function jsonRequest(url, pathName, options = {}) {
  const response = await fetch(new URL(pathName, url), {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(`${pathName} returned ${response.status}: ${payload.error || payload.detail || "unknown error"}`);
  return payload;
}

async function waitForValue(read, predicate, label, timeout = 90_000) {
  const deadline = Date.now() + timeout;
  let latest;
  let lastError;
  while (Date.now() < deadline) {
    try {
      latest = await read();
      if (predicate(latest)) return latest;
    } catch (error) { lastError = error; }
    await new Promise((resolve) => setTimeout(resolve, 300));
  }
  throw new Error(`Timed out waiting for ${label}.${lastError ? ` Last error: ${lastError.message}` : ""}`);
}
async function snapshotWhen(url, predicate, label, timeout) {
  return waitForValue(() => jsonRequest(url, "/api/snapshot"), predicate, label, timeout);
}

function validateHealth(health) {
  assert(health?.ok === true && String(health?.status).toLowerCase() === "healthy", "GroundGraph health must be healthy");
  assert(health.runtime_mode === "server", `recording requires runtime_mode=server, received ${health.runtime_mode}`);
  assert(health.llm?.enabled === true && health.llm?.model === "qwen3:4b-instruct", "local qwen3:4b-instruct must be enabled");
  assert(Number(health.agents?.specialist_count) === 6 && health.agents?.execution === "concurrent", "six concurrent specialists must be configured");
}

function loadScenes() {
  const scenes = JSON.parse(fs.readFileSync(path.join(repoRoot, "demo", "narration-scenes.json"), "utf8"));
  assert(Array.isArray(scenes), "narration scenes must be an array");
  assert(scenes.map((scene) => scene.id).join(",") === SCENE_ORDER.join(","), `scene order must be ${SCENE_ORDER.join(", ")}`);
  const totalWords = scenes.reduce((total, scene) => total + words(scene.narration).length, 0);
  assert(totalWords >= 330 && totalWords <= 380, `narration must be 330–380 words; found ${totalWords}`);
  for (const scene of scenes) {
    assert(scene.title && scene.narration, `${scene.id} needs a title and narration`);
    assert(Number(scene.minimum_seconds) >= 6, `${scene.id} needs minimum_seconds of at least six`);
  }
  return scenes;
}

function probeDuration(file) {
  return Number(command("ffprobe", ["-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", file], { capture: true }));
}

function prepareNarration(scenes, directory, voice, rate) {
  const audioDir = path.join(directory, "narration");
  fs.mkdirSync(audioDir, { recursive: true });
  return scenes.map((scene, index) => {
    const audio = path.join(audioDir, `${String(index).padStart(2, "0")}-${scene.id}.aiff`);
    command("say", ["-v", voice, "-r", String(rate), "-o", audio, scene.narration]);
    const audioDuration = probeDuration(audio);
    return { ...scene, audio, audioDuration, minimumDuration: Math.max(Number(scene.minimum_seconds), audioDuration + 1.5) };
  });
}

async function launchPage(chromium) {
  const browser = await chromium.launch({
    headless: true,
    executablePath: chromePath(),
    args: ["--hide-scrollbars", "--force-device-scale-factor=1", "--disable-dev-shm-usage"],
  });
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1, colorScheme: "dark" });
  const page = await context.newPage();
  return { browser, context, page };
}

async function visibleLocator(page, selector, timeout = 20_000) {
  const candidates = page.locator(selector);
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    const count = await candidates.count();
    for (let index = 0; index < count; index += 1) {
      const candidate = candidates.nth(index);
      if (await candidate.isVisible().catch(() => false)) return candidate;
    }
    await page.waitForTimeout(150);
  }
  throw new Error(`Could not find a visible element for ${selector}`);
}

async function injectDemoOverlay(page) {
  await page.evaluate(() => {
    for (const id of ["demo-recorder-style", "demo-recorder-backdrop", "demo-recorder-cursor", "demo-recorder-scene", "demo-recorder-focus", "demo-recorder-caption"]) document.getElementById(id)?.remove();
    const style = document.createElement("style");
    style.id = "demo-recorder-style";
    style.textContent = `
      .app-shell { position:relative !important; z-index:9001 !important; transform-origin:0 0 !important; transition:transform .68s cubic-bezier(.2,.8,.2,1),filter .35s !important; }
      #demo-recorder-backdrop { position:fixed; inset:0; z-index:9000; background-position:center; background-size:cover; filter:brightness(.42) saturate(.72) blur(1.2px); opacity:0; transition:opacity .28s; pointer-events:none; }
      #demo-recorder-cursor { position:fixed; left:0; top:0; z-index:9005; width:17px; height:17px; border:3px solid #fff; border-radius:50%; background:#45d8ff; box-shadow:0 0 0 7px rgba(69,216,255,.20),0 7px 24px rgba(0,0,0,.7); transform:translate(-50%,-50%); transition:left .48s cubic-bezier(.2,.8,.2,1),top .48s cubic-bezier(.2,.8,.2,1),opacity .2s,box-shadow .2s; pointer-events:none; }
      #demo-recorder-cursor.clicking { box-shadow:0 0 0 18px rgba(69,216,255,0),0 7px 24px rgba(0,0,0,.7); }
      #demo-recorder-scene { position:fixed; left:18px; top:15px; z-index:9004; display:flex; align-items:center; gap:9px; padding:9px 13px; border:1px solid rgba(69,216,255,.40); border-radius:999px; background:rgba(3,16,25,.92); color:#e9fbff; box-shadow:0 10px 32px rgba(0,0,0,.35); pointer-events:none; font:750 12px/1.1 ui-sans-serif,system-ui,sans-serif; letter-spacing:.02em; }
      #demo-recorder-scene i { width:7px; height:7px; border-radius:50%; background:#45d8ff; box-shadow:0 0 11px #45d8ff; }
      #demo-recorder-scene small { color:#75edbd; font:850 9px/1 ui-monospace,SFMono-Regular,monospace; letter-spacing:.11em; }
      #demo-recorder-focus { position:fixed; z-index:9002; display:block; border:2px solid rgba(69,216,255,.82); border-radius:15px; box-shadow:0 0 0 9999px rgba(1,8,15,.48),0 0 35px rgba(69,216,255,.22); opacity:0; transition:left .45s,top .45s,width .45s,height .45s,opacity .25s; pointer-events:none; }
      #demo-recorder-caption { position:fixed; z-index:9006; left:50%; bottom:24px; width:min(1080px,calc(100vw - 80px)); min-height:58px; display:grid; place-items:center; padding:10px 22px; border:1px solid rgba(166,226,244,.34); border-radius:13px; color:#fff; background:rgba(2,10,17,.94); box-shadow:0 14px 38px rgba(0,0,0,.58); opacity:0; transform:translate(-50%,8px); transition:opacity .18s,transform .18s; pointer-events:none; text-align:center; font:750 21px/1.34 ui-sans-serif,system-ui,sans-serif; letter-spacing:.005em; text-wrap:balance; }
      #demo-recorder-caption.visible { opacity:1; transform:translate(-50%,0); }
    `;
    document.head.append(style);
    const backdrop = document.createElement("div");
    backdrop.id = "demo-recorder-backdrop";
    const cursor = document.createElement("div");
    cursor.id = "demo-recorder-cursor";
    cursor.style.left = "720px";
    cursor.style.top = "450px";
    const scene = document.createElement("div");
    scene.id = "demo-recorder-scene";
    scene.innerHTML = "<i></i><small>LIVE PROOF</small><span>GroundGraph</span>";
    const focus = document.createElement("div");
    focus.id = "demo-recorder-focus";
    const caption = document.createElement("div");
    caption.id = "demo-recorder-caption";
    caption.setAttribute("aria-hidden", "true");
    document.body.append(backdrop, cursor, scene, focus, caption);
  });
}

async function setSceneLabel(page, title) {
  await page.evaluate((label) => { const node = document.querySelector("#demo-recorder-scene span"); if (node) node.textContent = label; }, title);
}
async function setCursorVisible(page, visible) {
  await page.evaluate((show) => { const cursor = document.getElementById("demo-recorder-cursor"); if (cursor) cursor.style.opacity = show ? "1" : "0"; }, visible);
}
async function resetCamera(page, waitMs = 360) {
  await page.evaluate(() => {
    const shell = document.querySelector(".app-shell");
    if (shell) shell.style.transform = "translate(0px,0px) scale(1)";
    const focus = document.getElementById("demo-recorder-focus");
    if (focus) focus.style.opacity = "0";
    const backdrop = document.getElementById("demo-recorder-backdrop");
    if (backdrop) backdrop.style.opacity = "0";
  });
  if (waitMs) await page.waitForTimeout(waitMs);
}

async function captureCameraBackdrop(page) {
  const mask = ["#demo-recorder-cursor", "#demo-recorder-scene", "#demo-recorder-focus", "#demo-recorder-caption"].map((selector) => page.locator(selector));
  const pixels = await page.screenshot({ type: "jpeg", quality: 58, fullPage: false, mask, maskColor: "#06111a" });
  await page.evaluate((image) => {
    const backdrop = document.getElementById("demo-recorder-backdrop");
    if (backdrop) {
      backdrop.style.backgroundImage = `url(${image})`;
      backdrop.style.opacity = "1";
    }
  }, `data:image/jpeg;base64,${pixels.toString("base64")}`);
}

async function focusCamera(page, selector, options = {}) {
  const { zoom = 1.14, spotlight = true, waitMs = 850, padding = 12 } = options;
  await resetCamera(page, 0);
  const locator = await visibleLocator(page, selector);
  await locator.evaluate((node) => node.scrollIntoView({ block: "center", inline: "center", behavior: "smooth" }));
  await page.waitForTimeout(350);
  const box = await locator.boundingBox();
  if (!box) throw new Error(`Could not measure ${selector}`);
  await captureCameraBackdrop(page);
  const scale = Math.max(1, Math.min(Number(zoom), 1.28));
  await page.evaluate(({ x, y, scaleValue }) => {
    const shell = document.querySelector(".app-shell");
    if (shell) shell.style.transform = `translate(${x}px,${y}px) scale(${scaleValue})`;
  }, { x: 720 - (box.x + box.width / 2) * scale, y: 450 - (box.y + box.height / 2) * scale, scaleValue: scale });
  await page.waitForTimeout(700);
  const focusedBox = await locator.boundingBox();
  if (spotlight && focusedBox) {
    await page.evaluate(({ x, y, width, height, inset }) => {
      const focus = document.getElementById("demo-recorder-focus");
      if (!focus) return;
      const left = Math.max(8, x - inset); const top = Math.max(8, y - inset);
      const right = Math.min(innerWidth - 8, x + width + inset); const bottom = Math.min(innerHeight - 8, y + height + inset);
      Object.assign(focus.style, { left: `${left}px`, top: `${top}px`, width: `${Math.max(1, right - left)}px`, height: `${Math.max(1, bottom - top)}px`, opacity: "1" });
    }, { ...focusedBox, inset: padding });
  }
  await page.waitForTimeout(waitMs);
  return locator;
}

async function pointTo(page, selector, waitMs = 650) {
  const locator = await visibleLocator(page, selector);
  const box = await locator.boundingBox();
  if (!box) throw new Error(`Could not locate ${selector}`);
  await page.evaluate(({ x, y }) => {
    const cursor = document.getElementById("demo-recorder-cursor");
    if (cursor) { cursor.style.left = `${x}px`; cursor.style.top = `${y}px`; }
  }, { x: box.x + box.width / 2, y: box.y + box.height / 2 });
  await page.waitForTimeout(waitMs);
  return locator;
}
async function demoClick(page, selector, options = {}) {
  const locator = await pointTo(page, selector, options.pointMs ?? 520);
  await page.evaluate(() => document.getElementById("demo-recorder-cursor")?.classList.add("clicking"));
  await locator.click({ force: options.force ?? false });
  await page.waitForTimeout(240);
  await page.evaluate(() => document.getElementById("demo-recorder-cursor")?.classList.remove("clicking"));
  return locator;
}

async function ensurePaused(page) {
  await page.evaluate(() => {
    if (typeof window.setPlaying === "function") window.setPlaying(false);
    if (window.ui?.tickTimer) clearInterval(window.ui.tickTimer);
  }).catch(() => {});
  const play = page.locator(UI.play).first();
  if (await play.count() && await play.getAttribute("aria-pressed") === "true") await play.click();
}
async function waitUntilIdle(page, timeout = 90_000) {
  await page.waitForFunction(() => {
    const radar = document.querySelector("#replan-radar");
    return !document.querySelector('[aria-busy="true"]') && (!radar || !radar.classList.contains("running"));
  }, null, { timeout });
}

async function assertUiContract(page) {
  const required = [
    ["airport selector", UI.airportSelector], ["airport map", UI.airportMap], ["stage rail", UI.stageRail],
    ["decision story", UI.story], ["why-now section", UI.whyNow], ["agent findings", UI.findings],
    ["candidate options", UI.options], ["model choice", UI.modelChoice], ["safety checks", UI.safety],
    ["decision outcome", UI.outcome], ["decision log", UI.decisionLog], ["primary action", UI.primaryAction],
    ["jump control", UI.jump], ["play control", UI.play],
  ];
  for (const [label, selector] of required) assert(await page.locator(selector).count() > 0, `UI is missing ${label}: ${selector}`);
}

async function clickStoryPhase(page, phase) {
  await resetCamera(page, 220);
  const selector = `#stage-rail [data-phase="${phase}"], .story-rail [data-phase="${phase}"]`;
  const phaseControl = await visibleLocator(page, selector);
  await phaseControl.scrollIntoViewIfNeeded();
  await pointTo(page, selector, 280);
  await phaseControl.click({ force: true });
  const sectionByPhase = {
    trigger: "#why-now", findings: "#agent-findings", options: "#candidate-options",
    choice: "#model-choice", guards: "#safety-checks", outcome: "#decision-outcome", next: "#next-check",
  };
  await page.waitForFunction(({ expectedPhase, expectedSection }) => {
    const active = [...document.querySelectorAll("#decision-story > .story-section.story-active")];
    const rail = document.querySelector(`#stage-rail [data-phase="${expectedPhase}"]`);
    return active.length === 1 && active[0].matches(expectedSection) && rail?.getAttribute("aria-current") === "step";
  }, { expectedPhase: phase, expectedSection: sectionByPhase[phase] }, { timeout: 5_000 });
  await page.waitForTimeout(380);
}

function currentEpisode(snapshot) {
  const plan = snapshot.current_plan || {};
  return String(plan.episode_id || plan.decision_episode_id || "");
}
function history(snapshot) { return list(snapshot?.replanning?.history); }
async function getDecisionStory(url, episodeId) {
  assert(episodeId, "a decision episode id is required");
  return jsonRequest(url, `/api/decision-story?episode_id=${encodeURIComponent(episodeId)}`);
}

function assertProposedStory(story, snapshot) {
  assert(story.result_kind === "PROPOSED", `first decision must remain PROPOSED, received ${story.result_kind}`);
  assert(story.replay_time === "08:36", `ORD disruption must be shown at 08:36, received ${story.replay_time}`);
  assert(list(story.trigger?.event_ids).includes("EVT-BAG-0836"), "story must be scoped to EVT-BAG-0836");
  assert(story.why_now?.outbound_callsign === "UA2672", "why-now receipt must identify UA2672");
  assert(Number(story.why_now?.minutes_to_departure) === 49, "why-now receipt must show 49 minutes to departure");
  assert(Number(story.why_now?.critical_path_minutes) === 57, "why-now receipt must show the 57-minute critical path");
  assert(Number(story.why_now?.projected_slip_minutes) === 8, "why-now receipt must show the eight-minute gap");
  assert(list(story.findings).length === 6, "decision story must contain exactly six specialist findings");
  assert(new Set(story.findings.map((item) => item.agent_id)).size === 6, "findings must come from six unique agents");
  assert(story.findings.every((item) => Number(item.based_on_version) === Number(story.graph_version)), "all findings must use the decision graph version");
  assert(list(story.candidates).length >= 3, "judge story requires at least three safe candidate options");
  assert(story.selection?.source === "local_model", `judge story requires local_model, received ${story.selection?.source}`);
  assert(list(story.selection?.allowlisted_candidate_ids).length >= 2, "Qwen must receive at least two legitimate options in the judge story");
  assert(list(story.selection?.allowlisted_candidate_ids).includes(story.selection?.selected_candidate_id), "selected candidate must be in Qwen's allowlist");
  assert(list(story.guards).length === 7 && story.guards.every((guard) => guard.passed), "all seven guards must pass");
  const run = list(snapshot.model_trace).find((item) => String(item.episode_id) === String(story.episode_id));
  assert(run, `persisted ModelRun is missing for ${story.episode_id}`);
  const runAllowlist = list(run.allowlisted_candidate_ids).map(String).sort();
  const storyAllowlist = list(story.selection.allowlisted_candidate_ids).map(String).sort();
  assert(JSON.stringify(runAllowlist) === JSON.stringify(storyAllowlist), "visible allowlist must equal persisted ModelRun allowlist");
  assert(String(run.selected_candidate_id) === String(story.selection.selected_candidate_id), "visible selection must equal persisted ModelRun selection");
}

function assertAppliedStory(story, proposedStory) {
  assert(story.episode_id === proposedStory.episode_id, "application must update the inspected decision episode");
  assert(story.result_kind === "APPLIED" && story.outcome?.applied === true, `decision must be APPLIED, received ${story.result_kind}`);
  assert(Number(story.outcome?.baseline_delay_minutes) === 30, "applied outcome must retain the 30-minute baseline");
  assert(Number(story.outcome?.projected_delay_minutes) === 18, "applied outcome must project 18 downstream minutes");
  assert(Number(story.outcome?.recovered_minutes) === 12, "applied outcome must project 12 recovered minutes");
}

function findNoAction(snapshot, afterCycle) {
  return history(snapshot).find((item) => Number(item.cycle) > Number(afterCycle) && ["NO_ACTION_REQUIRED", "NO_CHANGE"].includes(String(item.status).toUpperCase()));
}
function assertNoActionStory(story) {
  assert(story.result_kind === "NO_ACTION", `restraint scene requires NO_ACTION, received ${story.result_kind}`);
  assert(String(story.no_action_reason || "").toLowerCase().includes("benefit threshold"), "no-action receipt must name the minimum-benefit guardrail");
  assert(list(story.candidates).length === 0, "no-action receipt must not invent candidate plans");
  const stages = new Map(list(story.stages).map((item) => [item.id, item.status]));
  assert(stages.get("model") === "skipped" && stages.get("applied") === "skipped", "no-action receipt must show model and apply skipped");
}

async function seekReplay(page, minute) {
  const timeline = await visibleLocator(page, "#timeline");
  await timeline.evaluate((node, target) => {
    node.value = String(target);
    node.dispatchEvent(new Event("input", { bubbles: true }));
    node.dispatchEvent(new Event("change", { bubbles: true }));
  }, minute);
}
async function clickDecisionEpisode(page, episodeId) {
  const locator = page.locator(`#decision-log .decision-log-card[data-episode="${String(episodeId).replaceAll('"', '\\"')}"]`).first();
  assert(await locator.count() > 0, `decision log has no row for ${episodeId}`);
  await locator.scrollIntoViewIfNeeded();
  await locator.click();
  await page.waitForFunction((id) => {
    const row = document.querySelector(`#decision-log .decision-log-card[data-episode="${CSS.escape(String(id))}"]`);
    const subtitle = document.querySelector("#story-subtitle")?.textContent || "";
    return row?.getAttribute("aria-pressed") === "true" && !subtitle.includes("Loading the episode-scoped explanation");
  }, episodeId, { timeout: 15_000 });
  await page.waitForTimeout(280);
}

function snapshotSignature(snapshot) {
  const resources = Array.isArray(snapshot.resources) ? snapshot.resources : Object.values(snapshot.resources || {}).flatMap((value) => list(value));
  return {
    scenarioId: snapshot.scenario?.id,
    airport: snapshot.scenario?.airport,
    graph: snapshot.graph?.name,
    flights: list(snapshot.flights).map((item) => item.id).sort(),
    resources: resources.map((item) => item?.id).filter(Boolean).sort(),
    display: snapshot.scenario?.display || snapshot.scenario?.airport_layout || {},
  };
}
function assertDistinctAirport(from, to, expectedCode) {
  assert(to.airport === expectedCode, `secondary airport must be ${expectedCode}, received ${to.airport}`);
  assert(to.scenarioId && to.scenarioId !== from.scenarioId, "secondary airport needs a distinct scenario package");
  assert(to.graph && to.graph !== from.graph, "secondary airport needs a distinct FalkorDB graph");
  assert(JSON.stringify(to.flights) !== JSON.stringify(from.flights), "secondary airport needs distinct flights");
  assert(JSON.stringify(to.resources) !== JSON.stringify(from.resources), "secondary airport needs distinct resources");
  assert(JSON.stringify(to.display) !== JSON.stringify(from.display), "secondary airport needs distinct map/weather display data");
}
async function selectAirportUi(page, code) {
  await demoClick(page, UI.airportButton);
  const optionSelector = `#airport-menu [data-airport="${code}"], #airport-selector [data-airport="${code}"]`;
  const option = await visibleLocator(page, optionSelector);
  await pointTo(page, optionSelector, 320);
  await option.click();
  await page.waitForFunction((airport) => document.querySelector("#airport-code")?.textContent?.trim() === airport, code, { timeout: 90_000 });
  await waitUntilIdle(page);
}

async function readOnlyDryRun(chromium, options) {
  const [snapshot, health, airports] = await Promise.all([
    jsonRequest(options.url, "/api/snapshot"),
    jsonRequest(options.url, "/api/health"),
    jsonRequest(options.url, "/api/airports"),
  ]);
  validateHealth(health);
  assert(list(airports.airports).length >= 2, "dry run requires at least two registered airports");
  loadScenes();
  const { browser, page } = await launchPage(chromium);
  try {
    await page.route("**/api/**", async (route) => {
      if (route.request().method() === "GET") await route.continue();
      else await route.fulfill({ status: 403, contentType: "application/json", body: JSON.stringify({ ok: false, error: "Demo dry run blocks mutations" }) });
    });
    await page.goto(options.url, { waitUntil: "networkidle", timeout: 30_000 });
    await page.waitForFunction(() => document.querySelector("#truth-badge")?.textContent?.trim() === "LIVE GRAPH STATE");
    await ensurePaused(page);
    await assertUiContract(page);
    await injectDemoOverlay(page);
    await setSceneLabel(page, "Read-only recording contract check");
    for (const phase of ["trigger", "findings", "options", "choice", "guards", "outcome", "next", "trigger"]) await clickStoryPhase(page, phase);
    await setCursorVisible(page, false);
    await focusCamera(page, UI.story, { zoom: 1.08, spotlight: true, waitMs: 300 });
    await page.evaluate(() => {
      const caption = document.getElementById("demo-recorder-caption");
      if (!caption) return;
      caption.textContent = "Every decision is explained before it can be applied.";
      caption.classList.add("visible");
    });
    await page.waitForTimeout(240);
    fs.mkdirSync(path.dirname(options.screenshot), { recursive: true });
    await page.screenshot({ path: options.screenshot, type: "png", fullPage: false });
    const report = await page.evaluate(() => ({
      viewport: [innerWidth, innerHeight],
      document: [document.documentElement.scrollWidth, document.documentElement.scrollHeight],
      truth: document.querySelector("#truth-badge")?.textContent?.trim(),
      airport: document.querySelector("#airport-code")?.textContent?.trim(),
      clock: document.querySelector("#replay-clock")?.textContent?.trim(),
      active_story_sections: document.querySelectorAll("#decision-story > .story-section.story-active").length,
      active_story_phase: document.querySelector("#stage-rail [aria-current='step']")?.dataset.phase,
      focus_backdrop: Boolean(document.querySelector("#demo-recorder-backdrop")?.style.backgroundImage),
      burned_caption_preview: document.querySelector("#demo-recorder-caption")?.classList.contains("visible") === true,
      caption_box: (() => {
        const node = document.querySelector("#demo-recorder-caption");
        if (!node) return null;
        const box = node.getBoundingClientRect(); const style = getComputedStyle(node);
        return { x: Math.round(box.x), y: Math.round(box.y), width: Math.round(box.width), height: Math.round(box.height), opacity: style.opacity, visibility: style.visibility, z_index: style.zIndex };
      })(),
    }));
    console.log(JSON.stringify({
      mode: "read-only dry run",
      screenshot: options.screenshot,
      active_scenario: snapshot.scenario?.id,
      airports: airports.airports.map((item) => item.code),
      ...report,
    }, null, 2));
  } finally { await browser.close(); }
}

function startScreencast(context, page, framesDirectory) {
  fs.mkdirSync(framesDirectory, { recursive: true });
  const records = [];
  let index = 0;
  let client;
  return {
    async start() {
      client = await context.newCDPSession(page);
      client.on("Page.screencastFrame", (event) => {
        const filename = `${String(index).padStart(6, "0")}.jpg`;
        fs.writeFileSync(path.join(framesDirectory, filename), Buffer.from(event.data, "base64"));
        records.push({ filename, timestamp: Number(event.metadata.timestamp) });
        index += 1;
        void client.send("Page.screencastFrameAck", { sessionId: event.sessionId }).catch(() => {});
      });
      await client.send("Page.startScreencast", { format: "jpeg", quality: 88, maxWidth: 1440, maxHeight: 900, everyNthFrame: 3 });
    },
    async stop() {
      await page.waitForTimeout(500);
      await client.send("Page.stopScreencast");
      assert(records.length >= 100, `screencast returned only ${records.length} frames`);
      return records;
    },
  };
}

function writeFrameConcat(records, framesDirectory, target) {
  const lines = ["ffconcat version 1.0"];
  const durations = [];
  for (let index = 0; index < records.length - 1; index += 1) {
    const raw = records[index + 1].timestamp - records[index].timestamp;
    const duration = Math.max(0.02, Math.min(0.25, raw || 0.07));
    durations.push(duration);
    lines.push(`file '${path.join(framesDirectory, records[index].filename).replaceAll("'", "'\\''")}'`);
    lines.push(`duration ${duration.toFixed(6)}`);
  }
  const last = path.join(framesDirectory, records.at(-1).filename).replaceAll("'", "'\\''");
  lines.push(`file '${last}'`);
  lines.push(`duration ${(durations.at(-1) || 0.07).toFixed(6)}`);
  lines.push(`file '${last}'`);
  fs.writeFileSync(target, `${lines.join("\n")}\n`);
}

function splitCaptions(text, maxWords = 12) {
  const sentences = String(text).match(/[^.!?]+[.!?]+|[^.!?]+$/g) || [String(text)];
  const chunks = [];
  for (const sentence of sentences) {
    const tokens = sentence.trim().split(/\s+/).filter(Boolean);
    for (let start = 0; start < tokens.length; start += maxWords) chunks.push(tokens.slice(start, start + maxWords).join(" "));
  }
  return chunks;
}

async function startSceneCaptions(page, scene) {
  const captions = splitCaptions(scene.narration);
  const captionWeights = captions.map((caption) => Math.max(1, words(caption).length));
  const totalWeight = captionWeights.reduce((sum, value) => sum + value, 0);
  const offsets = [];
  let elapsed = 650;
  for (const weight of captionWeights) {
    offsets.push(Math.round(elapsed));
    elapsed += scene.audioDuration * 1000 * (weight / totalWeight);
  }
  await page.evaluate(({ lines, starts, clearAt }) => {
    for (const timer of window.__groundgraphDemoCaptionTimers || []) clearTimeout(timer);
    const node = document.getElementById("demo-recorder-caption");
    if (!node) return;
    node.textContent = "";
    node.classList.remove("visible");
    const timers = lines.map((line, index) => setTimeout(() => {
      node.textContent = line;
      node.classList.add("visible");
    }, starts[index]));
    timers.push(setTimeout(() => node.classList.remove("visible"), clearAt));
    window.__groundgraphDemoCaptionTimers = timers;
  }, { lines: captions, starts: offsets, clearAt: Math.round(650 + scene.audioDuration * 1000 + 120) });
}

async function clearSceneCaptions(page) {
  await page.evaluate(() => {
    for (const timer of window.__groundgraphDemoCaptionTimers || []) clearTimeout(timer);
    window.__groundgraphDemoCaptionTimers = [];
    const node = document.getElementById("demo-recorder-caption");
    if (node) node.classList.remove("visible");
  });
}

function srtTime(seconds) {
  const milliseconds = Math.max(0, Math.round(seconds * 1000));
  const hours = Math.floor(milliseconds / 3_600_000);
  const minutes = Math.floor((milliseconds % 3_600_000) / 60_000);
  const secs = Math.floor((milliseconds % 60_000) / 1000);
  const millis = milliseconds % 1000;
  return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(secs).padStart(2, "0")},${String(millis).padStart(3, "0")}`;
}
function writeSubtitles(sceneRecords, target) {
  let sceneStart = 0;
  let cueIndex = 1;
  const lines = [];
  for (const scene of sceneRecords) {
    const captions = splitCaptions(scene.narration);
    const counts = captions.map((caption) => Math.max(1, words(caption).length));
    const total = counts.reduce((sum, value) => sum + value, 0);
    const narrationStart = sceneStart + 0.65;
    const available = Math.min(scene.audioDuration, Math.max(1, scene.duration - 0.8));
    let offset = 0;
    for (let index = 0; index < captions.length; index += 1) {
      const duration = available * (counts[index] / total);
      const start = narrationStart + offset;
      const end = Math.min(sceneStart + scene.duration - 0.05, start + duration - 0.04);
      lines.push(String(cueIndex), `${srtTime(start)} --> ${srtTime(end)}`, captions[index], "");
      cueIndex += 1;
      offset += duration;
    }
    sceneStart += scene.duration;
  }
  fs.writeFileSync(target, `${lines.join("\n")}\n`);
}
function renderVideoAndAudio(frames, framesDirectory, sceneRecords, workDirectory, output) {
  const frameConcat = path.join(workDirectory, "frames.ffconcat");
  const subtitleFile = path.join(workDirectory, "captions.srt");
  const silentVideo = path.join(workDirectory, "silent-captioned.mp4");
  writeFrameConcat(frames, framesDirectory, frameConcat);
  writeSubtitles(sceneRecords, subtitleFile);
  const videoFilter = "scale=1440:900:flags=lanczos,format=yuv420p";
  command("ffmpeg", [
    "-y", "-v", "warning", "-f", "concat", "-safe", "0", "-i", frameConcat,
    "-fps_mode", "vfr", "-vf", videoFilter, "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-movflags", "+faststart", silentVideo,
  ]);
  const paddedFiles = [];
  for (const [index, scene] of sceneRecords.entries()) {
    const padded = path.join(workDirectory, `audio-${String(index).padStart(2, "0")}.wav`);
    command("ffmpeg", [
      "-y", "-v", "warning", "-i", scene.audio, "-af", "adelay=650:all=1,apad", "-t", scene.duration.toFixed(3),
      "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", padded,
    ]);
    paddedFiles.push(padded);
  }
  const audioConcat = path.join(workDirectory, "audio.ffconcat");
  fs.writeFileSync(audioConcat, `ffconcat version 1.0\n${paddedFiles.map((file) => `file '${file.replaceAll("'", "'\\''")}'`).join("\n")}\n`);
  const narration = path.join(workDirectory, "narration.wav");
  command("ffmpeg", ["-y", "-v", "warning", "-f", "concat", "-safe", "0", "-i", audioConcat, "-c:a", "pcm_s16le", narration]);
  command("ffmpeg", [
    "-y", "-v", "warning", "-i", silentVideo, "-i", narration, "-map", "0:v:0", "-map", "1:a:0",
    "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
    "-ar", "48000", "-shortest", "-movflags", "+faststart", output,
  ]);
  return subtitleFile;
}

async function recordCandidate(chromium, options) {
  assert(options.allowMutations, "--record changes replay state; add --allow-mutations after the candidate is ready");
  const protectedFinal = path.resolve(repoRoot, "demo", "groundgraph-demo.mp4");
  assert(path.resolve(options.output) !== protectedFinal, "the recorder never overwrites demo/groundgraph-demo.mp4");
  assert(!fs.existsSync(options.output) || options.force, `${options.output} exists; choose another path or add --force`);
  requireCommand("ffmpeg");
  requireCommand("ffprobe");
  requireCommand("say");
  const initialHealth = await jsonRequest(options.url, "/api/health");
  validateHealth(initialHealth);
  const catalog = await jsonRequest(options.url, "/api/scenarios");
  const ordEntry = list(catalog.scenarios).find((item) => item.airport === "ORD");
  const secondaryEntry = list(catalog.scenarios).find((item) => item.airport === options.secondaryAirport);
  assert(ordEntry, "scenario registry must contain ORD");
  assert(secondaryEntry, `scenario registry must contain ${options.secondaryAirport}`);
  assert(ordEntry.id !== secondaryEntry.id && ordEntry.runtime_graph_name !== secondaryEntry.runtime_graph_name, "airport scenarios must use distinct graph identities");
  await jsonRequest(options.url, "/api/scenarios/select", { method: "POST", body: JSON.stringify({ scenario_id: ordEntry.id }) });
  await jsonRequest(options.url, "/api/reset", { method: "POST", body: JSON.stringify({ preserve_memory: false }) });
  await jsonRequest(options.url, "/api/autonomy", { method: "POST", body: JSON.stringify({ enabled: false }) });
  const health = await jsonRequest(options.url, "/api/health");
  validateHealth(health);
  const initial = await jsonRequest(options.url, "/api/snapshot");
  assert(initial.scenario?.airport === "ORD", "recording must start on ORD");
  assert(Number(initial.clock?.cycle) === 0 && !initial.current_plan, "recording must start at cycle zero without a plan");
  assert(Number(initial.memory?.episode_count || 0) === 0, "recording must start without rehearsal memory");
  const ordSignature = snapshotSignature(initial);

  const stamp = new Date().toISOString().replace(/[:.]/g, "-");
  const workDirectory = path.join(repoRoot, ".artifacts", "demo-pipeline", `recording-${stamp}`);
  const framesDirectory = path.join(workDirectory, "frames");
  fs.mkdirSync(framesDirectory, { recursive: true });
  fs.mkdirSync(path.dirname(options.output), { recursive: true });
  const scenes = prepareNarration(loadScenes(), workDirectory, options.voice, options.rate);

  const { browser, context, page } = await launchPage(chromium);
  const sceneRecords = [];
  const proof = { applied: {}, no_action: {}, airport_switch: {} };
  let frames = [];
  let proposedSnapshot;
  let proposedStory;
  let appliedStory;
  try {
    await page.goto(options.url, { waitUntil: "networkidle", timeout: 30_000 });
    await page.waitForFunction(() => document.querySelector("#truth-badge")?.textContent?.trim() === "LIVE GRAPH STATE");
    await ensurePaused(page);
    await assertUiContract(page);
    await injectDemoOverlay(page);
    const screencast = startScreencast(context, page, framesDirectory);
    await screencast.start();

    async function runScene(scene, action) {
      await setSceneLabel(page, scene.title);
      const started = Date.now();
      await startSceneCaptions(page, scene);
      const at = async (seconds) => {
        const remaining = seconds * 1000 - (Date.now() - started);
        if (remaining > 0) await page.waitForTimeout(remaining);
      };
      await action({ at });
      const elapsed = (Date.now() - started) / 1000;
      if (elapsed < scene.minimumDuration) await page.waitForTimeout((scene.minimumDuration - elapsed) * 1000);
      const duration = (Date.now() - started) / 1000;
      await clearSceneCaptions(page);
      sceneRecords.push({ ...scene, duration });
      console.log(`${scene.id}: ${duration.toFixed(1)}s visual, ${scene.audioDuration.toFixed(1)}s narration`);
    }

    await runScene(scenes[0], async ({ at }) => {
      await setCursorVisible(page, false);
      await focusCamera(page, UI.airportMap, { zoom: 1.04, spotlight: false, waitMs: 200 });
      await at(4.2);
      await setCursorVisible(page, true);
      await demoClick(page, UI.jump);
      proposedSnapshot = await snapshotWhen(options.url, (snapshot) => {
        const plan = snapshot.current_plan || {};
        return plan.status === "PROPOSED" && Boolean(currentEpisode(snapshot));
      }, "an ORD proposal at the baggage disruption");
      await waitUntilIdle(page);
      proposedStory = await getDecisionStory(options.url, currentEpisode(proposedSnapshot));
      assertProposedStory(proposedStory, proposedSnapshot);
      await setCursorVisible(page, false);
    });

    await runScene(scenes[1], async () => {
      await setCursorVisible(page, true);
      await clickStoryPhase(page, "trigger");
      await focusCamera(page, UI.whyNow, { zoom: 1.18, spotlight: true });
      await pointTo(page, UI.whyNow, 700);
      await setCursorVisible(page, false);
    });

    await runScene(scenes[2], async ({ at }) => {
      await setCursorVisible(page, true);
      await clickStoryPhase(page, "findings");
      await focusCamera(page, UI.findings, { zoom: 1.14, spotlight: true });
      for (const [second, agent] of [[4, "weather"], [9, "gate"], [14, "turn"], [18, "memory"]]) {
        await at(second);
        await pointTo(page, `#agent-findings [data-agent="${agent}"], .story-findings [data-agent="${agent}"]`, 420).catch(() => pointTo(page, UI.findings, 420));
      }
      await setCursorVisible(page, false);
    });

    await runScene(scenes[3], async ({ at }) => {
      await setCursorVisible(page, true);
      await clickStoryPhase(page, "options");
      await focusCamera(page, UI.options, { zoom: 1.14, spotlight: true });
      await at(6);
      await pointTo(page, "#story-candidates, #candidate-options", 800);
      await at(17);
      await resetCamera(page, 180);
      await clickStoryPhase(page, "choice");
      await focusCamera(page, UI.modelChoice, { zoom: 1.2, spotlight: true, waitMs: 300 });
      await pointTo(page, UI.modelChoice, 700);
      await setCursorVisible(page, false);
    });

    await runScene(scenes[4], async ({ at }) => {
      await setCursorVisible(page, true);
      await clickStoryPhase(page, "guards");
      await focusCamera(page, UI.safety, { zoom: 1.16, spotlight: true });
      await pointTo(page, UI.safety, 700);
      await at(11.5);
      await resetCamera(page, 300);
      await demoClick(page, UI.primaryAction);
      const appliedSnapshot = await snapshotWhen(options.url, (snapshot) => {
        const plan = snapshot.current_plan || {};
        return String(plan.episode_id) === String(proposedStory.episode_id) && plan.status === "APPLIED";
      }, "the validated plan to become applied");
      await waitUntilIdle(page);
      appliedStory = await getDecisionStory(options.url, proposedStory.episode_id);
      assertAppliedStory(appliedStory, proposedStory);
      assert(Number(appliedSnapshot.memory?.episode_count || 0) >= 1, "application must persist episodic memory");
      proof.applied = {
        episode_id: appliedStory.episode_id,
        plan_id: appliedStory.technical?.plan_id,
        graph_version: appliedStory.graph_version,
        findings: appliedStory.findings.length,
        allowlisted_candidates: appliedStory.selection.allowlisted_candidate_ids.length,
        guards_passed: appliedStory.guards.filter((item) => item.passed).length,
        recovered_minutes: appliedStory.outcome.recovered_minutes,
      };
      await resetCamera(page, 180);
      await clickStoryPhase(page, "outcome");
      await focusCamera(page, UI.outcome, { zoom: 1.18, spotlight: true, waitMs: 350 });
      await pointTo(page, UI.outcome, 700);
      await setCursorVisible(page, false);
    });

    await runScene(scenes[5], async ({ at }) => {
      await resetCamera(page, 200);
      await setCursorVisible(page, true);
      let noActionSnapshot;
      let noActionEntry;
      for (const target of [appliedStory.next_checkpoint.minute, appliedStory.next_checkpoint.minute + 5]) {
        await pointTo(page, "#timeline", 250);
        await seekReplay(page, target);
        noActionSnapshot = await snapshotWhen(
          options.url,
          (snapshot) => Boolean(findNoAction(snapshot, proposedStory.cycle)),
          `a no-action checkpoint by replay minute ${target}`,
          45_000,
        ).catch(() => null);
        if (noActionSnapshot) { noActionEntry = findNoAction(noActionSnapshot, proposedStory.cycle); break; }
      }
      assert(noActionEntry, "the continuous-replanning scene did not produce a real NO_ACTION entry");
      await waitUntilIdle(page);
      const noActionStory = await getDecisionStory(options.url, noActionEntry.episode_id);
      assertNoActionStory(noActionStory);
      await clickDecisionEpisode(page, noActionStory.episode_id);
      await clickStoryPhase(page, "trigger");
      await focusCamera(page, UI.whyNow, { zoom: 1.18, spotlight: true, waitMs: 300 });
      await pointTo(page, UI.whyNow, 500);
      await at(8);
      await clickStoryPhase(page, "choice");
      await focusCamera(page, UI.modelChoice, { zoom: 1.18, spotlight: true, waitMs: 250 });
      await at(13.5);
      await clickStoryPhase(page, "outcome");
      await focusCamera(page, UI.outcome, { zoom: 1.18, spotlight: true, waitMs: 250 });
      proof.no_action = {
        episode_id: noActionStory.episode_id,
        cycle: noActionStory.cycle,
        result_kind: noActionStory.result_kind,
        reason: noActionStory.no_action_reason,
        model_stage: noActionStory.stages.find((item) => item.id === "model")?.status,
        apply_stage: noActionStory.stages.find((item) => item.id === "applied")?.status,
      };
      await setCursorVisible(page, false);
    });

    await runScene(scenes[6], async ({ at }) => {
      await resetCamera(page, 150);
      await setCursorVisible(page, true);
      await clickDecisionEpisode(page, appliedStory.episode_id);
      const memoryTab = page.locator('[data-tab="memory"]').first();
      if (await memoryTab.count()) {
        await demoClick(page, '[data-tab="memory"]');
        await focusCamera(page, "#memory-stage", { zoom: 1.13, spotlight: true, waitMs: 250 });
      } else await focusCamera(page, UI.outcome, { zoom: 1.14, spotlight: true, waitMs: 250 });
      await at(10);
      const close = page.locator("#proof-close").first();
      if (await close.count() && await close.isVisible()) await close.click();
      await focusCamera(page, ".brain-card", { zoom: 1.13, spotlight: true, waitMs: 250 });
      const question = "Who owns the current gate reassignment policy?";
      await demoClick(page, `[data-brain-question="${question}"]`);
      await page.waitForSelector("#brain-answer .brain-response", { timeout: 30_000 });
      const answer = await jsonRequest(options.url, "/api/company-brain", { method: "POST", body: JSON.stringify({ question }) });
      assert(answer.answer_type === "OWNERSHIP_AND_AUTHORITY", "Operations Brain must return ownership and authority");
      assert(list(answer.evidence_ids).length >= 4 && list(answer.graph_path).length >= 3, "Operations Brain answer must include graph evidence and path");
      await pointTo(page, "#brain-answer .brain-response", 800);
      await setCursorVisible(page, false);
    });

    await runScene(scenes[7], async ({ at }) => {
      await resetCamera(page, 150);
      await setCursorVisible(page, true);
      await focusCamera(page, UI.airportSelector, { zoom: 1.17, spotlight: true, waitMs: 250 });
      await selectAirportUi(page, options.secondaryAirport);
      const secondarySnapshot = await snapshotWhen(options.url, (snapshot) => snapshot.scenario?.airport === options.secondaryAirport, `${options.secondaryAirport} to become active`);
      const secondarySignature = snapshotSignature(secondarySnapshot);
      assertDistinctAirport(ordSignature, secondarySignature, options.secondaryAirport);
      await focusCamera(page, UI.airportMap, { zoom: 1.04, spotlight: false, waitMs: 300 });
      await at(8.5);
      await focusCamera(page, UI.airportSelector, { zoom: 1.17, spotlight: true, waitMs: 180 });
      await selectAirportUi(page, "ORD");
      const restoredOrd = await snapshotWhen(options.url, (snapshot) => snapshot.scenario?.airport === "ORD", "ORD to be restored");
      assert(restoredOrd.graph?.name === ordSignature.graph, "returning to ORD must restore its original FalkorDB graph");
      assert(history(restoredOrd).some((item) => String(item.episode_id) === String(appliedStory.episode_id)), "returning to ORD must retain the applied decision episode");
      proof.airport_switch = {
        from: ordSignature.airport,
        to: secondarySignature.airport,
        from_graph: ordSignature.graph,
        to_graph: secondarySignature.graph,
        distinct_flights: JSON.stringify(ordSignature.flights) !== JSON.stringify(secondarySignature.flights),
        distinct_resources: JSON.stringify(ordSignature.resources) !== JSON.stringify(secondarySignature.resources),
        ord_episode_restored: true,
      };
      await setCursorVisible(page, false);
    });

    await runScene(scenes[8], async () => {
      await clickDecisionEpisode(page, appliedStory.episode_id);
      await clickStoryPhase(page, "outcome");
      await page.evaluate(() => window.scrollTo({ top: 0, behavior: "smooth" }));
      await resetCamera(page, 500);
      await setCursorVisible(page, false);
      await page.evaluate(() => { const focus = document.getElementById("demo-recorder-focus"); if (focus) focus.style.opacity = "0"; });
      await page.screenshot({ path: options.output.replace(/\.mp4$/i, "-cover.jpg"), type: "jpeg", quality: 94, fullPage: false });
    });

    frames = await screencast.stop();
  } finally { await browser.close(); }

  const subtitleFile = renderVideoAndAudio(frames, framesDirectory, sceneRecords, workDirectory, options.output);
  const videoDuration = probeDuration(options.output);
  assert(videoDuration >= 140 && videoDuration <= 190, `candidate must be 2:20–3:10; received ${videoDuration.toFixed(1)} seconds`);
  const digest = crypto.createHash("sha256").update(fs.readFileSync(options.output)).digest("hex");
  const manifest = {
    created_at: new Date().toISOString(),
    source_url: options.url,
    viewport: "1440x900",
    health: { runtime_mode: health.runtime_mode, graph: health.graph, endpoint: health.endpoint, llm: health.llm, agents: health.agents },
    narration: {
      voice: options.voice,
      rate: options.rate,
      word_count: scenes.reduce((total, scene) => total + words(scene.narration).length, 0),
      burned_in_subtitles: true,
    },
    semantic_proof: proof,
    scenes: sceneRecords.map(({ id, title, narration, audioDuration, duration }) => ({
      id, title, narration, audio_duration_seconds: audioDuration, visual_duration_seconds: duration,
    })),
    output: path.relative(repoRoot, options.output),
    duration_seconds: videoDuration,
    sha256: digest,
    frame_count: frames.length,
  };
  fs.writeFileSync(options.output.replace(/\.mp4$/i, ".manifest.json"), `${JSON.stringify(manifest, null, 2)}\n`);
  if (!options.keepWork) fs.rmSync(workDirectory, { recursive: true, force: true });
  console.log(JSON.stringify({
    candidate: options.output,
    duration_seconds: videoDuration,
    sha256: digest,
    cover: options.output.replace(/\.mp4$/i, "-cover.jpg"),
    subtitles: options.keepWork ? subtitleFile : "burned into candidate",
    semantic_proof: proof,
  }, null, 2));
}

const options = parseArgs(process.argv.slice(2));
if (options.mode === "help") {
  showHelp();
  process.exit(0);
}
const { chromium } = findPlaywright();
if (options.mode === "record") await recordCandidate(chromium, options);
else await readOnlyDryRun(chromium, options);
