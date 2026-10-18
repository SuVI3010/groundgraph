# Demo video pipeline

The recorder creates a **1440×900 review candidate** from the running release. It drives the real interface, focuses one decision step at a time, burns readable subtitles into the picture, and stores the API evidence used to validate every narrated claim.

For the founder-led final cut, use the conversational script, face-shot plan, and current benchmark sequence in [Human-led final demo guide](human-video-guide.md).

It never overwrites `demo/groundgraph-demo.mp4`. Candidate review and promotion remain separate steps.

## Dependencies

- the full local GroundGraph stack with FalkorDB and Ollama;
- Google Chrome or Chromium;
- Node.js 20 or newer;
- Playwright 1.62.1 or newer;
- macOS `say` when generated narration is used; and
- `ffmpeg` and `ffprobe`;
- Pillow 11.3.0 for the founder-led title cards and evidence cover.

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

### Use standard human narration

Pass `--narration-dir` to replace `say` with one recording per scene. The
directory must contain exactly one supported audio file for each canonical
stem. Supported extensions are `.wav`, `.m4a`, `.aiff`, `.aif`, and `.mp3`:

```text
00-hook.wav
01-why-now.wav
02-findings.wav
03-options.wav
04-apply.wav
05-restraint.wav
06-memory-knowledge.wav
07-airports.wav
08-close.wav
```

The extension may differ by clip, but do not provide two files with the same
stem. Read the corresponding text in `demo/narration-scenes.json` verbatim so
the burned captions match the recording. Trim long silence at the beginning
and end, keep the microphone position consistent, and leave natural pauses
inside the sentence.

Validate the clips, hashes, readable durations, and minimum planned runtime
without changing replay state:

```bash
./scripts/record_demo.sh \
  --dry-run \
  --narration-dir=demo/narration-human
```

Then record the candidate:

```bash
./scripts/record_demo.sh \
  --record \
  --allow-mutations \
  --force \
  --narration-dir=demo/narration-human \
  --secondary-airport=ATL \
  --output=demo/groundgraph-human-candidate.mp4 \
  --keep-work
```

The manifest identifies the narration source and records each clip's basename,
duration, and SHA-256 hash. Scene visuals still wait for the longer of the
declared minimum or the recorded clip plus 1.5 seconds. This retains the same
captions, evidence checks, and scene order as generated narration. A single
continuous master track is not accepted because live API and model latency can
change individual scene lengths.

### Founder-synchronised final cut

The published founder-led demo uses the pause-marked copy in
[`demo/human-recording-script.md`](../demo/human-recording-script.md). The raw
camera and Voice Memos takes are personal editing inputs and stay local rather
than entering Git history. Put them in `demo/` with these names:

```text
1.mov          opening face take
missed.m4a     why-now narration
2.m4a          six findings
3.m4a          options and local Qwen
4.m4a          seven checks and application
5.m4a          deliberate no-action cycle
6.m4a          memory and Company Brain
7.m4a          ATL and SFO reuse
8.mov          benchmark and founder close
```

Install the development tools, prepare cleaned canonical narration, and verify
all nine clips without changing replay state:

```bash
python3.12 -m pip install -r requirements-dev.txt
python3.12 scripts/prepare_human_media.py
./scripts/record_demo.sh \
  --dry-run \
  --sync-profile=founder \
  --narration-dir=.artifacts/human-media/narration
```

Capture the working interface with actions timed to the recorded phrases:

```bash
./scripts/record_demo.sh \
  --record \
  --allow-mutations \
  --force \
  --keep-work \
  --sync-profile=founder \
  --narration-dir=.artifacts/human-media/narration \
  --output=demo/groundgraph-founder-screen-candidate.mp4
```

After reviewing the screen capture, add the face opening and close:

```bash
python3.12 scripts/assemble_founder_video.py \
  --candidate=demo/groundgraph-founder-screen-candidate.mp4 \
  --opening=demo/1.mov \
  --closing=demo/8.mov \
  --output=demo/groundgraph-founder-candidate.mp4 \
  --force
```

The compositor preserves the verified narration audio bit-for-bit, rejects
duration or source-checksum drift, carries the runtime and semantic evidence
into the edited manifest, performs decode/black-frame/silence/media checks, and
generates an evidence-led cover. The local raw takes and intermediate founder
candidates are ignored by Git; only the reviewed promoted demo, cover, and
manifest are published.

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

The manifest records runtime health, narration source, clip hashes, word count, scene timings, frame count, SHA-256, and semantic proof for the applied episode, deliberate no-action episode, and distinct airport graphs.

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
