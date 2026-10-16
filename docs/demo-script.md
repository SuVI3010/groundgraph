# Judge demo script

Target length: **2:30–2:50**. Tell one complete decision story. The full-screen airport view establishes context; every later shot focuses one explanation step at readable size. The recording is a bounded research replay, not a live airport-control claim.

## Recording gate

Run `make verify`, `make demo-check`, and `make audit` first. The recorder then enforces the claims that matter to the story:

- the official FalkorDB server and local `qwen3:4b-instruct` are healthy;
- ORD begins at cycle zero with no plan or rehearsal memory;
- the 08:36 baggage episode exposes the 49-minute departure horizon, 57-minute task path, and eight-minute projected gap;
- six unique specialists return findings from the episode's exact graph version;
- at least three safe options exist and at least two legitimate identifiers reach Qwen;
- the displayed shortlist and selection equal the persisted `ModelRun` values;
- all seven guards pass before one manual application;
- application produces the modeled `30 → 18` downstream-delay comparison and 12 projected minutes recovered;
- a later decision-log entry is genuinely `NO_ACTION`, names the minimum-benefit guardrail, and marks model and apply as skipped;
- Operations Brain returns ownership, authority, evidence IDs, and a graph path; and
- ATL uses a distinct scenario, FalkorDB graph, flights, resources, and airport display before ORD is restored with its prior decision intact.

If any assertion fails, stop. Fix the product or narration instead of recording around it.

## Storyboard

### 0:00–0:13 — The disruption

Begin on the moving ORD surface without a cursor. After four seconds, reveal the cursor and choose **Jump to first disruption**. Do not show projected improvement before the plan is applied.

> At eight thirty-six at Chicago O'Hare, a modeled baggage delay adds twelve minutes to U A fourteen fourteen's turnaround, putting U A twenty-six seventy-two at risk. GroundGraph must recover time without breaking policy.

### 0:13–0:30 — Why now

Choose **Detected** on the stage rail. Focus `#why-now`. Show the 09:25 departure, 49 minutes remaining, 57-minute connected task path, and eight-minute gap.

> Why now? U A twenty-six seventy-two leaves at nine twenty-five, forty-nine minutes away, but its turnaround needs fifty-seven. The eight-minute gap opens a FalkorDB decision episode and locks all six specialists to one graph version.

### 0:30–0:51 — Six graph specialists

Choose **Read graph**. Focus `#agent-findings` and point to Weather, Gate, Turnaround, and Memory as examples. All six cards must be complete before the coordinator continues.

> Six specialists read that state in parallel. Weather checks conditions. Flow checks runway capacity. Gate checks compatibility. Turnaround finds the delayed task chain. Policy confirms authority. Memory searches matching outcomes. The coordinator waits until all six versioned findings are stored.

### 0:51–1:18 — Options and Qwen

Choose **Compared** and focus the three option cards. Then choose **Qwen** and focus `#model-choice`. The video must show at least two real allowlisted IDs. If only one remains, do not describe the model as making a tradeoff choice.

> Now the tradeoff is visible. Turnaround recovery saves twelve minutes with four minutes of coordination. Flow balance saves twelve but spends runway flexibility. Gate recovery moves the rotation and saves six. The server validates and ranks all three before Qwen sees their identifiers. Qwen selects one allowlisted fit, and its choice is stored beside the shortlist.

### 1:18–1:44 — Validate and apply

Choose **Verified** and focus the seven checks. Click **Apply validated plan** once. Choose **Result** only after the API reports the same episode as applied. Focus the modeled comparison as it changes from 30 to 18.

> The model cannot execute. The validator rereads graph version, target links, authority, knowledge freshness, benefit, execution freeze, and churn. Seven checks pass. One click writes the actions and modeled outcome to FalkorDB. Only now does projected delay move from thirty to eighteen minutes: twelve recovered under replay assumptions.

### 1:44–2:04 — Deliberate no action

Advance to the next checkpoint and select the new `.decision-log-card.no-action` row. Return to **Detected** so the minimum-benefit reason is readable, then choose **Qwen** to show that model selection was skipped and **Result** to show that the operation held steady. The episode receipt must state `NO_ACTION`, cite the minimum-benefit guardrail, contain no candidate plan, and mark both model and apply as skipped.

> At the next checkpoint, GroundGraph reads again. The projected benefit is now below the four-minute benefit threshold. The log records no action; model selection and application are skipped. Continuous replanning includes knowing when not to disturb a stable operation.

### 2:04–2:28 — Memory and company knowledge

Reopen the applied episode, open its Memory proof, then ask **Who owns gate recovery?** Focus the returned owner, role, procedure, source section, and path.

> The outcome becomes memory tied to event class, rotation, resources, and policy. A matching disruption may use it; unrelated weather cannot. Operations Brain follows the same graph to name the gate-recovery owner, responsible role, procedure, source section, and evidence path. Memory and company knowledge remain inspectable.

### 2:28–2:41 — Another airport

Use the visible picker to switch ORD to ATL. Show the changed map and airport context. Switch back to ORD and show that its applied episode remains in its own graph.

> GroundGraph is not hard-coded to O'Hare. Atlanta loads a distinct FalkorDB graph with different flights, runways, weather, and evidence. Returning to O'Hare restores its applied decision.

### 2:41–2:48 — Close

Return to the full interface with the applied ORD receipt selected. Hide the cursor.

> One shared graph. Six specialists. A bounded local model. Every intervention explained before it is applied.

## Presentation rules

- Use stage-rail clicks to reveal one decision step at a time.
- Keep technical IDs and Cypher in the proof drawer; the primary story stays in plain language.
- Use the recorder's camera focus, dimmed full-screen backdrop, and spotlight instead of scrolling a small dense panel.
- Burn the timed browser captions into the captured frames so the story remains understandable without audio.
- Say **projected under replay assumptions**, never actual real-world savings.
- Attribute public schedule, weather, and runway fields accurately; gates, operational effects, policies, actions, and outcomes remain modeled.
- Show Qwen as a bounded selector. Deterministic code constructs and validates candidates; the model cannot invent or execute actions.
