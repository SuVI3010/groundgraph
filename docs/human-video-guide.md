# Human-led final demo guide

Target length: **2:40–2:50**. The final video should feel like one founder explaining one airport problem, not a narrated product tour. Begin and end with a real person on camera. Use the product only when it supplies evidence for the sentence being spoken.

This is an editing guide for a new human version. It does not replace or modify `demo/groundgraph-demo.mp4`, `demo/narration-scenes.json`, or the validated automated recorder. Keep those files as the reproducible proof package.

## Recording approach

1. Record the opening and closing face shots first. Use an eye-level camera, soft front light, a quiet room, and the same framing for both.
2. Record the nine narration takes below in a natural voice at roughly 140–145 words per minute. Look at a person just behind the camera rather than reading every word straight down a page.
3. Capture the current UI at 1440×900 or higher. Hide notifications and browser chrome, keep the product between 110% and 125% zoom, and let the cursor rest unless it is pointing to the evidence being described.
4. Edit around the speech. Use direct cuts and short dissolves only; do not add a long logo animation, stock-airport montage, or synthetic presenter.
5. Burn accurate two-line captions into the final export. Keep music optional and quiet enough that every word remains clear.

Record at 48 kHz. A clean mono voice recording is fine; the final export can remain the pipeline's 48 kHz stereo AAC. Aim for approximately -16 LUFS integrated loudness and no peaks above -1 dBTP.

## Narration filenames and two editing paths

Use these nine scene IDs to keep the takes aligned with the verified product footage:

| Order | Scene ID | Canonical filename |
|---:|---|---|
| 1 | `hook` | `00-hook.wav` |
| 2 | `why-now` | `01-why-now.wav` |
| 3 | `findings` | `02-findings.wav` |
| 4 | `options` | `03-options.wav` |
| 5 | `apply` | `04-apply.wav` |
| 6 | `restraint` | `05-restraint.wav` |
| 7 | `memory-knowledge` | `06-memory-knowledge.wav` |
| 8 | `airports` | `07-airports.wav` |
| 9 | `close` | `08-close.wav` |

For the **fastest automatic voice replacement**, put those clips in one directory and pass `--narration-dir`. WAV, M4A, AIFF, AIF, and MP3 are accepted. Read the existing `demo/narration-scenes.json` text verbatim so its burned captions match. The recorder retains the same verified UI actions, waits for each human clip, validates all nine files before changing replay state, and records their hashes in the candidate manifest.

For the **strongest founder-led cut**, use the revised script below and edit the opening face shot, completed real-day scorecard, 30-day metrics card, and closing face shot around the verified product footage. This wording intentionally differs from `demo/narration-scenes.json`, so generate fresh captions in the editor instead of reusing the automated captions. Keep these filenames as an editing convention, and do not overwrite the packaged demo until the new export has been reviewed.

## Final shot list and exact script

### 0:00–0:16 — Human opening

**Narration file:** `00-hook.wav`

**Shot:** One founder, full screen, looking into the lens. Start immediately on the first word. Put `GroundGraph` and the one-line subtitle `Airport recovery through connected agents` on screen after two seconds.

**Say:**

> An airport delay rarely stays one delay. A late bag can hold a departure, block a gate, and affect the next flight. We built GroundGraph to see that chain, and help teams respond before it spreads.

**Cut:** Cut on the word “spreads” directly to the moving O'Hare surface. Do not insert a logo bumper.

### 0:16–0:31 — One connected airport state

**Narration file:** `01-why-now.wav`

**Shot:** Let the O'Hare replay move for two seconds. Point once to the airport clock, then use **Jump to first disruption**. Keep the top-down airport visible while the 08:36 baggage event appears.

**Say:**

> This is O'Hare in replay. Flights, gates, ground tasks, policies, and earlier decisions are connected in FalkorDB. At eight thirty-six, a baggage turnaround slips, putting a departure and its rotation at risk.

**On-screen label:** `FalkorDB · shared operational context`

### 0:31–0:49 — Six specialists, one version

**Narration file:** `02-findings.wav`

**Shot:** Open the episode's **Read graph** stage. Show all six completed specialist cards, then move slowly across Weather, Flow, Gate, Turnaround, Policy, and Memory. Avoid opening raw Cypher or technical identifiers.

**Say:**

> Six agents check the same graph version: weather, flow, gates, turnaround, policy, and memory. Their findings and handoffs are saved in FalkorDB, so we can see what each knew and what the coordinator actually used.

**On-screen label:** `Track 1 · Agents acting on connected data`

### 0:49–1:12 — Safe alternatives and bounded Qwen

**Narration file:** `03-options.wav`

**Shot:** Reveal **Compared**, then **Qwen**. Show the three materially different options and the allowlisted selection at readable size. Leave each option visible long enough to understand the tradeoff; do not read every number aloud.

**Say:**

> GroundGraph creates three recovery options from those connections. Local Qwen cannot invent an action. It can choose only from the safe shortlist already checked against the graph. The highlighted option stays inside that boundary, and its causal path explains why it fits this disruption.

**On-screen label:** `Qwen · explain and select only from safe IDs`

### 1:12–1:29 — Seven checks before action

**Narration file:** `04-apply.wav`

**Shot:** Select **Verified**, show the seven passed guards, apply once, and reveal **Result** only after the receipt appears. Hold on the modeled `30 → 18` comparison.

**Say:**

> The model still cannot execute. Seven checks re-read resources, policy, timing, benefit, and graph state. Only after they pass does GroundGraph write the actions and modeled outcome to FalkorDB: thirty minutes becomes eighteen under the replay assumptions.

**Small disclosure:** `Projected result under replay assumptions`

### 1:29–1:44 — Continuous replanning can stop

**Narration file:** `05-restraint.wav`

**Shot:** Advance to the next checkpoint and open the genuine no-action entry. Show that model selection and application were skipped. Keep the minimum-benefit reason in view.

**Say:**

> At the next checkpoint, the agents read again. The remaining benefit is below the safety threshold, so GroundGraph deliberately does nothing. Continuous replanning also means knowing when an intervention would create more churn than value.

**On-screen label:** `Track 2 · Coordination and contextual memory`

### 1:44–2:03 — Memory and Company Brain

**Narration file:** `06-memory-knowledge.wav`

**Shot:** Reopen the applied episode's memory proof. Then ask **Who owns gate recovery?** in Operations Brain and highlight the owner, responsible role, current procedure, source section, and evidence path.

**Say:**

> The outcome becomes memory tied to the event, rotation, resources, and policy. It can influence a later plan only when that context matches. If an operator asks who owns this recovery, Company Brain follows the graph from policy to procedure, team, role, and source evidence.

**On-screen label:** `Track 3 · Company Brain with cited evidence`

### 2:03–2:09 — The same framework, another airport

**Narration file:** `07-airports.wav`

**Shot:** Use the visible airport selector to flash ATL and SFO, then return to ORD. Keep this to six seconds; the goal is to prove separate graphs, not to demo each scenario again.

**Say:**

> The same decision contract also runs on separate Atlanta and San Francisco graphs.

### 2:09–2:48 — Historical evidence and human close

**Narration file:** `08-close.wav`

**Shot sequence:**

- **2:09–2:19:** Switch to **Real-day test** and show the completed July 27 scorecard. Use a short speed-ramped clock sequence rather than showing the full replay.
- **2:19–2:36:** Cut to one clean metrics card for the 30-day benchmark. Reveal the counts first, then the comparison.
- **2:36–2:48:** Return to the founder on camera. Finish with the GroundGraph name and repository URL beside, not over, the speaker's face.

**Say:**

> FalkorDB holds shared state, handoffs, policy, memory, decisions, and evidence across all three tracks. We also tested whether the forecast tracks reality. The replay uses only information available at each hourly cutoff, then reveals the recorded result. Across thirty July days, six hundred thirty-eight forecasts, and fifty-four thousand, seven hundred seventy-four movements, average hourly delay error fell from thirty-four point nine two to nineteen point zero seven minutes. This is a one-airport retrospective benchmark, not a claim of real-world minutes saved. It shows a transparent path from disruption to an evidence-backed response. That's GroundGraph.

**Real-day overlay:**

- `July 27 · hardest cohort day`
- `24 forecast episodes · 144 agent handoffs`
- `1,996 scored movements`
- `Delay MAE: 77.0 min vs 162.5 min schedule baseline`

**30-day overlay:**

- `30 days · 638 hourly forecasts · 54,774 movements`
- `Delay MAE: 19.07 min vs 34.92 min baseline`
- `Disruption MAE: 9.42 pp vs 30.68 pp baseline`
- `Delay-band accuracy: 65.83%`

## What to cut

Cut anything that competes with the decision story:

- installation, terminals, Podman startup, FalkorDB Browser, raw Cypher, and source-code scrolling;
- a logo animation longer than one second;
- reading every option score or all seven guard names aloud;
- repeated passes over the six agent cards;
- the full 24-hour replay at normal speed;
- dense benchmark tables when one comparison card communicates the result;
- long ATL and SFO walkthroughs;
- claims that the replay is live airport control or that the modeled recovery saved real minutes; and
- an AI avatar or synthetic founder voice.

The current UI should remain on screen only while it proves the statement being spoken. When a section has made its point, cut immediately to the next piece of evidence.

## Human delivery notes

- Speak as if explaining the project to one judge standing beside you. A small smile and one natural hand gesture are better than a formal pitch voice.
- Pause briefly after “FalkorDB,” “seven checks,” and each benchmark comparison. Do not slow down to pronounce every decimal digit dramatically.
- Keep one or two natural breaths. Remove mistakes and long silences, but do not edit every sentence into a perfectly uniform rhythm.
- Use one speaker throughout. If both founders appear, let the second founder operate the demo silently or deliver a complete section rather than alternating every sentence.
- Keep captions to two lines and highlight only the current phrase or number.
- Watch the final export once without sound. The three tracks, Qwen's bounded role, the guarded action, the real-day test, and the 30-day result should still be understandable.

## Final factual checks before export

- The runtime badge shows the full official FalkorDB server.
- The screen shows six completed specialist findings from one graph version.
- Qwen sees only the displayed allowlisted candidate IDs and cannot apply a plan.
- All seven guards pass before the modeled outcome appears.
- The no-action episode is genuine and records model/application as skipped.
- Operations Brain returns a visible evidence path or `INSUFFICIENT_EVIDENCE`.
- The July 27 and 30-day metrics match the checked-in benchmark artifacts.
- The video calls recovery results projected and the benchmark retrospective.
- The final repository URL and YouTube visibility are readable and correct.
