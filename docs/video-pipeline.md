# Demo video pipeline

The recorder produces the 1440×900 submission video from the running release
candidate. It drives the visible interface rather than fabricating screenshots,
and records the exact local-model and FalkorDB results returned during the run.

## Dependencies

- Google Chrome or Chromium
- Node.js 20 or newer
- Playwright 1.62.1 or newer
- macOS `say`
- `ffmpeg` and `ffprobe`

The wrapper finds the Playwright copy bundled with Codex when it is available.
On another machine, install only the automation library and reuse local Chrome:

```bash
PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 npm install --no-save playwright@1.62.1
```

## Read-only smoke test

With the full local stack running, check the viewport and browser rendering:

```bash
./scripts/record_demo.sh --dry-run
```

The smoke test stubs every browser POST with the current snapshot. It does not
reset, advance, or otherwise mutate the FalkorDB replay. Its screenshot is
written to `.artifacts/demo-pipeline/dry-run.png`.

## Record a review candidate

First run the repository's normal verification and demo checks. Then record:

```bash
./scripts/record_demo.sh \
  --record \
  --allow-mutations \
  --output=demo/groundgraph-demo-candidate.mp4
```

The explicit mutation flag is required because the recording clears rehearsal
memory, changes server autonomy, advances the replay, applies a plan, resets
with preserved memory, and publishes the synthetic policy update. Before doing
so, the script verifies all of the following:

- the API is healthy;
- `runtime_mode` is `server`;
- the configured local model is `qwen3:4b-instruct`; and
- six specialists report concurrent execution.

The final run demonstrates the disruption, six handoffs, local-model selection,
causal path, field-level provenance, guarded application, persistent contextual
memory, Operations Brain, policy supersession, and the 09:04 replan. The visual
stream comes from Chrome's DevTools screencast at 1440×900. Narration is created
per scene with `say`, normalized to broadcast-friendly loudness, and muxed with
H.264 video and AAC audio by `ffmpeg`.

The output is accompanied by:

- `groundgraph-demo-candidate-cover.jpg`, captured from the final interface;
- `groundgraph-demo-candidate.manifest.json`, containing runtime evidence,
  scene timings, narration, frame count, duration, and the video SHA-256 digest.

The recorder refuses to overwrite `demo/groundgraph-demo.mp4`. Watch the entire
candidate, inspect the manifest, and only then replace the submission asset.
Use `--keep-work` when frame-level debugging is needed; otherwise the temporary
JPEG and uncompressed-audio files are deleted after a successful encode.
