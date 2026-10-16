# Demo video pipeline

The recorder creates a **1440×900 review candidate** from the running release. It drives the real interface, focuses one decision step at a time, burns readable subtitles into the picture, and stores the API evidence used to validate every narrated claim.

It never overwrites `demo/groundgraph-demo.mp4`. Candidate review and promotion remain separate steps.

## Dependencies

- the full local GroundGraph stack with FalkorDB and Ollama;
- Google Chrome or Chromium;
- Node.js 20 or newer;
- Playwright 1.62.1 or newer;
- macOS `say`; and
- `ffmpeg` and `ffprobe`.

The wrapper can use the Playwright copy bundled with Codex. On another machine:

```bash
PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 npm install --no-save playwright@1.62.1
```

## Read-only UI and contract check

Start the complete stack, then run:

```bash
./scripts/record_demo.sh --dry-run
```

This permits live GET requests and blocks every browser mutation. It verifies the official-server health contract, the multi-airport catalog, the 330–380-word narration, and all required linear-story selectors. It writes a focused screenshot to `.artifacts/demo-pipeline/dry-run.png`.

## Record a candidate

The default target uses Daniel at 185 words per minute and switches ORD to ATL:

```bash
./scripts/record_demo.sh \
  --record \
  --allow-mutations \
  --force \
  --voice=Daniel \
  --rate=185 \
  --secondary-airport=ATL \
  --output=demo/groundgraph-demo-candidate.mp4
```

Expected duration is **about 2:40–2:50**. Runtime model latency may extend a scene, but the finished candidate must remain between 2:20 and 3:10 or the recorder rejects it.

The explicit mutation flag is required because the run:

1. selects and clears the ORD replay;
2. disables automatic application;
3. advances to the modeled 08:36 baggage disruption;
4. applies the validated proposal once;
5. advances to a real no-action checkpoint;
6. asks Operations Brain a bounded ownership question;
7. switches to a distinct ATL graph; and
8. returns to the preserved ORD graph.

Before capturing each claim, the recorder checks the backing API state. It fails rather than producing a video if the decision episode, six handoffs, Qwen allowlist, model selection, seven guards, outcome, no-action reason, Operations Brain evidence, or airport isolation differs from the narration.

## Visual and audio treatment

- The browser remains 1440×900.
- Stage-rail controls reveal one decision step at a time.
- Camera transforms, a dimmed full-screen backdrop, and a spotlight enlarge the active explanation region without empty frame edges.
- The cursor appears only for meaningful interactions.
- Captions are generated from the exact scene narration and drawn into the captured browser frames. This keeps them burned into the H.264 picture even when FFmpeg was built without `libass`; the equivalent SRT is retained with `--keep-work`.
- Scene narration is padded, normalized to `-16 LUFS`, and muxed as 48 kHz AAC.

The candidate produces:

- `groundgraph-demo-candidate.mp4`;
- `groundgraph-demo-candidate-cover.jpg`; and
- `groundgraph-demo-candidate.manifest.json`.

The manifest records runtime health, narration settings and word count, scene timings, frame count, SHA-256, and semantic proof for the applied episode, deliberate no-action episode, and distinct airport graphs.

Use `--keep-work` to retain captured frames, intermediate audio, and the generated SRT file. Without it, temporary material is removed only after the candidate and manifest are complete.

## Review before promotion

Watch the whole candidate at normal player size and once without audio. A judge should be able to identify:

- what changed and why the decision happened at that moment;
- what six specialists contributed;
- which options existed and why one won;
- what Qwen was allowed to choose;
- which checks gated execution;
- the modeled result;
- why the next cycle deliberately did nothing;
- how memory and company knowledge remain connected; and
- how another airport uses a distinct FalkorDB graph.

Also run full decode, black-frame, silence, and subtitle checks. Promote the candidate only after visual review and after its manifest matches the promoted filename and source commit.
