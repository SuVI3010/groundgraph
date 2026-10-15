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

function parseArgs(argv) {
  const options = {
    mode: "dry-run",
    url: "http://127.0.0.1:5050/",
    voice: "Samantha",
    rate: "190",
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
    else if (argument.startsWith("--output=")) options.output = path.resolve(repoRoot, argument.slice("--output=".length));
    else if (argument.startsWith("--screenshot=")) options.screenshot = path.resolve(repoRoot, argument.slice("--screenshot=".length));
    else if (argument === "--help" || argument === "-h") options.mode = "help";
    else throw new Error(`Unknown argument: ${argument}`);
  }
  return options;
}

function showHelp() {
  console.log(`GroundGraph demo recorder

Safe read-only smoke test (default):
  ./scripts/record_demo.sh --dry-run

Final candidate recording (resets and advances the replay graph):
  ./scripts/record_demo.sh --record --allow-mutations

Options:
  --url=http://127.0.0.1:5050/
  --output=demo/groundgraph-demo-candidate.mp4
  --voice=Samantha --rate=190
  --screenshot=.artifacts/demo-pipeline/dry-run.png
  --keep-work   Retain captured JPEG frames and intermediate audio.
  --force       Replace an existing candidate output.

The recorder refuses to write demo/groundgraph-demo.mp4. Review the candidate,
then replace the submission asset separately.`);
}

function findPlaywright() {
  const candidates = [
    process.env.GROUNDGRAPH_PLAYWRIGHT,
    "playwright",
    path.join(os.homedir(), ".cache", "codex-runtimes", "codex-primary-runtime", "dependencies", "node", "node_modules", "playwright"),
  ].filter(Boolean);
  for (const candidate of candidates) {
    try {
      return require(candidate);
    } catch {
      // Try the next configured installation.
    }
  }
  throw new Error(
    "Playwright was not found. Run: PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 npm install --no-save playwright@1.62.1",
  );
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

function requireCommand(name) {
  command("/usr/bin/env", ["sh", "-c", `command -v ${name}`], { capture: true });
}

async function jsonRequest(url, pathName, options = {}) {
  const response = await fetch(new URL(pathName, url), {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(`${pathName} returned ${response.status}: ${payload.error || payload.detail || "unknown error"}`);
  return payload;
}

function validateHealth(health) {
  if (health?.ok !== true || String(health?.status).toLowerCase() !== "healthy") throw new Error("GroundGraph health is not healthy.");
  if (health.runtime_mode !== "server") throw new Error(`Recording requires runtime_mode=server; received ${health.runtime_mode}.`);
  if (health.llm?.enabled !== true || health.llm?.model !== "qwen3:4b-instruct") {
    throw new Error("Recording requires enabled local qwen3:4b-instruct.");
  }
  if (Number(health.agents?.specialist_count) !== 6 || health.agents?.execution !== "concurrent") {
    throw new Error("Recording requires six concurrent specialists.");
  }
}

function loadScenes() {
  const file = path.join(repoRoot, "demo", "narration-scenes.json");
  const scenes = JSON.parse(fs.readFileSync(file, "utf8"));
  const expected = ["overview", "coordination", "decision", "application", "memory", "knowledge", "continuous"];
  if (!Array.isArray(scenes) || scenes.map((scene) => scene.id).join(",") !== expected.join(",")) {
    throw new Error(`Narration scenes must be ordered as: ${expected.join(", ")}`);
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
    return { ...scene, audio, audioDuration, minimumDuration: audioDuration + 2.0 };
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

async function injectDemoOverlay(page) {
  await page.evaluate(() => {
    const style = document.createElement("style");
    style.textContent = `
      #demo-recorder-cursor { position:fixed; left:0; top:0; z-index:2147483647; width:15px; height:15px; border:2px solid #fff; border-radius:50%; background:#45d8ff; box-shadow:0 0 0 5px rgba(69,216,255,.18),0 5px 18px rgba(0,0,0,.5); transform:translate(-50%,-50%); transition:left .55s cubic-bezier(.2,.8,.2,1),top .55s cubic-bezier(.2,.8,.2,1),box-shadow .2s; pointer-events:none; }
      #demo-recorder-cursor.clicking { box-shadow:0 0 0 13px rgba(69,216,255,0),0 5px 18px rgba(0,0,0,.5); }
      #demo-recorder-scene { position:fixed; left:15px; bottom:14px; z-index:2147483646; display:flex; align-items:center; gap:8px; padding:7px 10px; border:1px solid rgba(69,216,255,.32); border-radius:8px; background:rgba(3,16,25,.88); color:#dffaff; box-shadow:0 8px 28px rgba(0,0,0,.28); pointer-events:none; font:700 9px/1.1 ui-sans-serif,system-ui,sans-serif; letter-spacing:.04em; }
      #demo-recorder-scene i { width:6px; height:6px; border-radius:50%; background:#45d8ff; box-shadow:0 0 9px #45d8ff; }
      #demo-recorder-scene small { color:#75edbd; font:800 7px/1 ui-monospace,SFMono-Regular,monospace; letter-spacing:.1em; }
    `;
    document.head.append(style);
    const cursor = document.createElement("div"); cursor.id = "demo-recorder-cursor"; cursor.style.left = "720px"; cursor.style.top = "450px";
    const scene = document.createElement("div"); scene.id = "demo-recorder-scene"; scene.innerHTML = "<i></i><small>LIVE DEMO</small><span>GroundGraph</span>";
    document.body.append(cursor, scene);
  });
}

async function setSceneLabel(page, title) {
  await page.evaluate((label) => {
    const node = document.querySelector("#demo-recorder-scene span");
    if (node) node.textContent = label;
  }, title);
}

async function pointTo(page, selector, waitMs = 850) {
  const locator = page.locator(selector).first();
  await locator.waitFor({ state: "visible", timeout: 20_000 });
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
  const locator = await pointTo(page, selector, options.pointMs ?? 650);
  await page.evaluate(() => document.getElementById("demo-recorder-cursor")?.classList.add("clicking"));
  await locator.click({ force: options.force ?? false });
  await page.waitForTimeout(220);
  await page.evaluate(() => document.getElementById("demo-recorder-cursor")?.classList.remove("clicking"));
  return locator;
}

async function waitUntilIdle(page, timeout = 90_000) {
  await page.waitForFunction(() => !document.querySelector("#replan-radar")?.classList.contains("running"), null, { timeout });
}

async function selectTab(page, name) {
  await demoClick(page, `[data-tab="${name}"]`);
  await page.waitForFunction((tab) => document.querySelector(`[data-tab="${tab}"]`)?.getAttribute("aria-selected") === "true", name);
}

async function waitForLocalPlan(page, timeout = 90_000) {
  await page.waitForFunction(() => {
    const selector = document.querySelector("#plan-selector")?.textContent || "";
    return selector.includes("LOCAL QWEN SELECTED") || selector.includes("DETERMINISTIC");
  }, null, { timeout });
  const selector = (await page.locator("#plan-selector").textContent())?.trim();
  if (selector !== "LOCAL QWEN SELECTED") throw new Error(`Expected local-model selection; interface reported ${selector}.`);
}

async function assertPersistedModelAllowlist(url) {
  const snapshot = await jsonRequest(url, "/api/snapshot");
  const plan = snapshot.current_plan || {};
  const shortlist = (plan.model_candidate_set || []).map((item) => String(item.id)).sort();
  if (!plan.episode_id || shortlist.length === 0) {
    throw new Error("Current plan is missing its decision episode or non-empty model_candidate_set.");
  }
  const run = (snapshot.model_trace || []).find((item) => item.episode_id === plan.episode_id);
  if (!run) throw new Error(`No persisted ModelRun was found for ${plan.episode_id}.`);
  const allowlist = (run.allowlisted_candidate_ids || []).map(String).sort();
  if (JSON.stringify(shortlist) !== JSON.stringify(allowlist)) {
    throw new Error(`Plan shortlist ${JSON.stringify(shortlist)} does not match persisted ModelRun allowlist ${JSON.stringify(allowlist)}.`);
  }
  if (!allowlist.includes(String(plan.candidate_id)) || run.selected_candidate_id !== plan.candidate_id) {
    throw new Error("The persisted ModelRun selection is outside its allowlist or differs from the visible plan.");
  }
}

async function readOnlyDryRun(chromium, options) {
  const [snapshot, health] = await Promise.all([
    jsonRequest(options.url, "/api/snapshot"),
    jsonRequest(options.url, "/api/health"),
  ]);
  validateHealth(health);
  const { browser, page } = await launchPage(chromium);
  try {
    await page.route("**/api/**", async (route) => {
      const pathname = new URL(route.request().url()).pathname;
      const payload = pathname === "/api/health" ? health : snapshot;
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(payload) });
    });
    await page.goto(options.url, { waitUntil: "networkidle", timeout: 30_000 });
    await page.waitForFunction(() => document.querySelector("#truth-badge")?.textContent?.trim() === "LIVE GRAPH STATE");
    await page.evaluate(() => { setPlaying(false); clearInterval(ui.tickTimer); });
    await injectDemoOverlay(page);
    await pointTo(page, "#graph-status", 300);
    fs.mkdirSync(path.dirname(options.screenshot), { recursive: true });
    await page.screenshot({ path: options.screenshot, type: "png", fullPage: false });
    const report = await page.evaluate(() => ({
      viewport: [innerWidth, innerHeight],
      document: [document.documentElement.scrollWidth, document.documentElement.scrollHeight],
      truth: document.querySelector("#truth-badge")?.textContent?.trim(),
      graph: document.querySelector("#graph-status")?.innerText?.replace(/\s+/g, " ").trim(),
      model: document.querySelector("#model-status")?.innerText?.replace(/\s+/g, " ").trim(),
      selector: document.querySelector("#plan-selector")?.textContent?.trim(),
      clock: document.querySelector("#replay-clock")?.textContent?.trim(),
    }));
    console.log(JSON.stringify({ mode: "read-only dry run", screenshot: options.screenshot, ...report }, null, 2));
  } finally {
    await browser.close();
  }
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
        void client.send("Page.screencastFrameAck", { sessionId: event.sessionId }).catch(() => {
          // Chrome may close immediately after Page.stopScreencast; every frame is
          // already on disk before its acknowledgement is sent.
        });
      });
      await client.send("Page.startScreencast", { format: "jpeg", quality: 84, maxWidth: 1440, maxHeight: 900, everyNthFrame: 4 });
    },
    async stop() {
      await page.waitForTimeout(500);
      await client.send("Page.stopScreencast");
      if (records.length < 100) throw new Error(`Screencast returned only ${records.length} frames.`);
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

function renderVideoAndAudio(frames, framesDirectory, sceneRecords, workDirectory, output) {
  const frameConcat = path.join(workDirectory, "frames.ffconcat");
  const silentVideo = path.join(workDirectory, "silent.mp4");
  writeFrameConcat(frames, framesDirectory, frameConcat);
  command("ffmpeg", ["-y", "-v", "warning", "-f", "concat", "-safe", "0", "-i", frameConcat, "-fps_mode", "vfr", "-vf", "scale=1440:900:flags=lanczos,format=yuv420p", "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-movflags", "+faststart", silentVideo]);

  const paddedFiles = [];
  for (const [index, scene] of sceneRecords.entries()) {
    const padded = path.join(workDirectory, `audio-${String(index).padStart(2, "0")}.wav`);
    command("ffmpeg", ["-y", "-v", "warning", "-i", scene.audio, "-af", "adelay=650:all=1,apad", "-t", scene.duration.toFixed(3), "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", padded]);
    paddedFiles.push(padded);
  }
  const audioConcat = path.join(workDirectory, "audio.ffconcat");
  fs.writeFileSync(audioConcat, `ffconcat version 1.0\n${paddedFiles.map((file) => `file '${file.replaceAll("'", "'\\''")}'`).join("\n")}\n`);
  const narration = path.join(workDirectory, "narration.wav");
  command("ffmpeg", ["-y", "-v", "warning", "-f", "concat", "-safe", "0", "-i", audioConcat, "-c:a", "pcm_s16le", narration]);
  command("ffmpeg", ["-y", "-v", "warning", "-i", silentVideo, "-i", narration, "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-ar", "48000", "-shortest", "-movflags", "+faststart", output]);
}

async function recordCandidate(chromium, options) {
  if (!options.allowMutations) throw new Error("--record changes replay state. Add --allow-mutations after the release candidate is ready.");
  const protectedFinal = path.resolve(repoRoot, "demo", "groundgraph-demo.mp4");
  if (path.resolve(options.output) === protectedFinal) throw new Error("The recorder will not overwrite demo/groundgraph-demo.mp4. Write a candidate and review it first.");
  if (fs.existsSync(options.output) && !options.force) throw new Error(`${options.output} already exists. Use a new path or add --force.`);
  requireCommand("ffmpeg"); requireCommand("ffprobe"); requireCommand("say");
  const health = await jsonRequest(options.url, "/api/health");
  validateHealth(health);

  const stamp = new Date().toISOString().replace(/[:.]/g, "-");
  const workDirectory = path.join(repoRoot, ".artifacts", "demo-pipeline", `recording-${stamp}`);
  const framesDirectory = path.join(workDirectory, "frames");
  fs.mkdirSync(framesDirectory, { recursive: true });
  fs.mkdirSync(path.dirname(options.output), { recursive: true });
  const scenes = prepareNarration(loadScenes(), workDirectory, options.voice, options.rate);

  await jsonRequest(options.url, "/api/reset", { method: "POST", body: JSON.stringify({ preserve_memory: false }) });
  await jsonRequest(options.url, "/api/autonomy", { method: "POST", body: JSON.stringify({ enabled: false }) });

  const { browser, context, page } = await launchPage(chromium);
  const sceneRecords = [];
  let frames = [];
  try {
    await page.goto(options.url, { waitUntil: "networkidle", timeout: 30_000 });
    await page.waitForFunction(() => document.querySelector("#truth-badge")?.textContent?.trim() === "LIVE GRAPH STATE");
    await page.evaluate(() => setPlaying(false));
    await injectDemoOverlay(page);
    const screencast = startScreencast(context, page, framesDirectory);
    await screencast.start();

    async function runScene(scene, action) {
      await setSceneLabel(page, scene.title);
      const started = Date.now();
      await action();
      const elapsed = (Date.now() - started) / 1000;
      if (elapsed < scene.minimumDuration) await page.waitForTimeout((scene.minimumDuration - elapsed) * 1000);
      const duration = (Date.now() - started) / 1000;
      sceneRecords.push({ ...scene, duration });
      console.log(`${scene.id}: ${duration.toFixed(1)}s visual, ${scene.audioDuration.toFixed(1)}s narration`);
    }

    await runScene(scenes[0], async () => {
      await pointTo(page, "#graph-status", 1500);
      await pointTo(page, "#model-status", 1500);
      await pointTo(page, ".provenance-legend", 1800);
      await pointTo(page, "#airport-view", 1200);
    });

    await runScene(scenes[1], async () => {
      await demoClick(page, "#jump-button");
      await waitUntilIdle(page);
      await waitForLocalPlan(page);
      await assertPersistedModelAllowlist(options.url);
      await selectTab(page, "cycles");
      await pointTo(page, ".cycles-stage", 1700);
      await page.locator(".cycles-stage").evaluate((node) => node.scrollTo({ top: node.scrollHeight, behavior: "smooth" }));
      await page.waitForTimeout(1800);
      await page.locator(".cycles-stage").evaluate((node) => node.scrollTo({ top: 0, behavior: "smooth" }));
    });

    await runScene(scenes[2], async () => {
      await selectTab(page, "plan");
      await pointTo(page, "#plan-selector", 1200);
      await pointTo(page, ".action-list", 1600);
      await pointTo(page, "#causal-path", 1800);
      await selectTab(page, "evidence");
      const details = page.locator(".record-provenance details").first();
      if (await details.count()) { await demoClick(page, ".record-provenance details summary"); await pointTo(page, ".record-provenance details[open]", 1600); }
      const cover = options.output.replace(/\.mp4$/i, "-cover.jpg");
      await page.screenshot({ path: cover, type: "jpeg", quality: 92, fullPage: false });
    });

    await runScene(scenes[3], async () => {
      await selectTab(page, "plan");
      await demoClick(page, "#auto-apply", { force: true });
      await waitUntilIdle(page);
      await page.waitForFunction(() => document.querySelector("#plan-kicker")?.textContent?.includes("APPLIED"), null, { timeout: 30_000 });
      await pointTo(page, "#plan-kicker", 1600);
      await demoClick(page, "#auto-apply", { force: true });
      await selectTab(page, "memory");
      await pointTo(page, ".memory-stage", 1600);
    });

    await runScene(scenes[4], async () => {
      await demoClick(page, "#reset-button");
      await page.waitForFunction(() => document.querySelector("#replay-clock")?.textContent?.trim() === "08:00", null, { timeout: 30_000 });
      await page.waitForTimeout(500);
      await page.evaluate(() => setPlaying(false));
      await selectTab(page, "memory");
      await page.waitForFunction(() => Number(document.querySelector("#memory-count")?.textContent || 0) > 0);
      await pointTo(page, ".memory-stage", 1600);
      await demoClick(page, "#jump-button");
      await waitUntilIdle(page);
      await waitForLocalPlan(page);
      await assertPersistedModelAllowlist(options.url);
      await selectTab(page, "plan");
      await pointTo(page, "#plan-reason", 1800);
    });

    await runScene(scenes[5], async () => {
      await demoClick(page, '[data-brain-question*="Who owns"]');
      await page.waitForFunction(() => document.querySelector(".brain-response")?.textContent?.includes("Who owns"), null, { timeout: 30_000 });
      await pointTo(page, ".brain-response", 2200);
      await demoClick(page, '[data-brain-question*="What happened"]');
      await page.waitForFunction(() => document.querySelector(".brain-response")?.textContent?.includes("What happened"), null, { timeout: 30_000 });
      await pointTo(page, ".brain-response", 1800);
      await selectTab(page, "evidence");
      await demoClick(page, ".publish-policy-update");
      await waitUntilIdle(page);
      await page.waitForSelector(".policy-demo-control.complete", { timeout: 90_000 });
      await pointTo(page, ".policy-demo-control.complete", 1800);
      await demoClick(page, ".view-policy-cycle");
      await pointTo(page, ".cycles-stage", 1300);
    });

    await runScene(scenes[6], async () => {
      await pointTo(page, "#timeline", 900);
      await page.locator("#timeline").evaluate((node) => {
        node.value = "64";
        node.dispatchEvent(new Event("input", { bubbles: true }));
        node.dispatchEvent(new Event("change", { bubbles: true }));
      });
      await page.waitForFunction(() => document.querySelector("#replay-clock")?.textContent?.trim() === "09:04", null, { timeout: 90_000 });
      await waitUntilIdle(page);
      await selectTab(page, "cycles");
      await pointTo(page, ".cycles-stage", 1800);
      await pointTo(page, ".track-proof", 2200);
      await pointTo(page, "#causal-path", 1400);
    });

    frames = await screencast.stop();
  } finally {
    await browser.close();
  }

  renderVideoAndAudio(frames, framesDirectory, sceneRecords, workDirectory, options.output);
  const videoDuration = probeDuration(options.output);
  const digest = crypto.createHash("sha256").update(fs.readFileSync(options.output)).digest("hex");
  const manifest = {
    created_at: new Date().toISOString(),
    source_url: options.url,
    viewport: "1440x900",
    health: { runtime_mode: health.runtime_mode, graph: health.graph, endpoint: health.endpoint, llm: health.llm, agents: health.agents },
    narration: { voice: options.voice, rate: options.rate },
    scenes: sceneRecords.map(({ id, title, narration, audioDuration, duration }) => ({ id, title, narration, audio_duration_seconds: audioDuration, visual_duration_seconds: duration })),
    output: path.relative(repoRoot, options.output),
    duration_seconds: videoDuration,
    sha256: digest,
    frame_count: frames.length,
  };
  fs.writeFileSync(options.output.replace(/\.mp4$/i, ".manifest.json"), `${JSON.stringify(manifest, null, 2)}\n`);
  if (!options.keepWork) fs.rmSync(workDirectory, { recursive: true, force: true });
  console.log(JSON.stringify({ candidate: options.output, duration_seconds: videoDuration, sha256: digest, cover: options.output.replace(/\.mp4$/i, "-cover.jpg") }, null, 2));
}

const options = parseArgs(process.argv.slice(2));
if (options.mode === "help") {
  showHelp();
  process.exit(0);
}
const { chromium } = findPlaywright();
if (options.mode === "record") await recordCandidate(chromium, options);
else await readOnlyDryRun(chromium, options);
