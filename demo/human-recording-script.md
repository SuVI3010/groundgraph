# GroundGraph human recording script

Target final length: **2:50–3:00**. Record each numbered section as a separate file. Do not speak anything inside square brackets. Keep the marked pauses because they provide room for the matching interface action.

## Recording rules

- Record at 48 kHz in one quiet room with the same microphone distance.
- Speak naturally at roughly 140–150 words per minute.
- Leave no more than 0.15 seconds before the first word and 0.3–0.5 seconds after the final word.
- Record two takes of every clip and keep the more relaxed one.
- Do not add music to the recordings.
- Say every number exactly as written.
- Clips 00 and 08 include the face-to-product and product-to-face transitions. No separate face audio is required.

## 00-hook.wav · face to airport · 0:00–0:18

**Picture:** Begin on the presenter at eye level. Show a small name and `GroundGraph` lower third after two seconds. At “A late bag,” cut directly to the moving ORD airport view while the same voice continues.

> An airport delay rarely stays one delay. [pause 0.4 seconds] A late bag can hold a departure, block a gate, and affect the next flight. [pause 0.5 seconds] I'm Viswesh Suri, and together with Sai Pranav, I built GroundGraph to see that chain and help teams respond before it spreads. [tail 0.3 seconds]

## 01-why-now.wav · disruption trigger · 0:18–0:36

**Picture:** Let the ORD map move for two seconds, then use **Jump to first disruption**. Make the 08:36 event appear as “At eight thirty-six” is spoken. Hold the connected rotation risk through the final phrase.

> This is O'Hare in replay. [pause 0.4 seconds] Flights, gates, ground tasks, policies, and earlier decisions are connected in FalkorDB. [pause 0.6 seconds] At eight thirty-six, a baggage turnaround slips, putting a departure and its rotation at risk. [tail 0.3 seconds]

## 02-findings.wav · six graph specialists · 0:36–0:54

**Picture:** Open **Read graph** before the first word. Sweep across Weather and Flow, Gate and Turnaround, then Policy and Memory. End on the persisted coordinator handoff receipt.

> Six agents check the same graph version: weather, flow, gates, turnaround, policy, and memory. [pause 0.5 seconds] Their findings and handoffs are saved in FalkorDB, [pause 0.3 seconds] so we can see what each knew and what the coordinator actually used. [tail 0.4 seconds]

## 03-options.wav · alternatives and Qwen · 0:54–1:14

**Picture:** Show **Compared** at the first word. Reveal all three plan cards when “three recovery options” is spoken. Switch to **Qwen** on “It can choose only,” then finish on the selected safe ID and its causal graph path.

> GroundGraph creates three recovery options from those connections. [pause 0.4 seconds] Local Qwen cannot invent an action. [pause 0.35 seconds] It can only choose from the safe shortlist already checked against the graph. [pause 0.55 seconds] The highlighted option stays inside that boundary, and its causal path explains why it fits this disruption. [tail 0.4 seconds]

## 04-apply.wav · guards and result · 1:14–1:34

**Picture:** Show the seven guards on “Seven checks.” Immediately after “Only after they pass,” apply the plan and wait for the receipt. Reveal `30 → 18` exactly as those values are spoken. Keep `Projected under replay assumptions` visible.

> The model still cannot execute. [pause 0.4 seconds] Seven checks re-read resources, policy, timing, benefit, and graph state. [action pause 0.7 seconds] Only after they pass does GroundGraph write the actions and modeled outcome to FalkorDB. [pause 0.35 seconds] Thirty minutes becomes eighteen under the replay assumptions. [tail 0.4 seconds]

## 05-restraint.wav · deliberate no action · 1:34–1:51

**Picture:** Advance to the next genuine checkpoint. Show the below-threshold reason, then that model selection and application were skipped. End by holding the no-action explanation.

> At the next checkpoint, the agents read again. [pause 0.35 seconds] The remaining benefit is below the safety threshold, so GroundGraph deliberately does nothing. [pause 0.45 seconds] Continuous replanning also means knowing when an intervention would create more churn than value. [tail 0.3 seconds]

## 06-memory-knowledge.wav · Tracks 2 and 3 · 1:51–2:11

**Picture:** Show contextual memory for the first half. At “If an operator asks,” switch to Operations Brain. Reveal owner, procedure, team, role, and source evidence in the order spoken.

> The outcome becomes memory tied to the event, rotation, resources, and policy. [pause 0.4 seconds] It can influence a later plan only when that context matches. [transition pause 0.55 seconds] If an operator asks who owns this recovery, Company Brain follows the graph from policy to procedure, team, role, and source evidence. [tail 0.4 seconds]

## 07-airports.wav · generalization beat · 2:11–2:17

**Picture:** Briefly flash ATL and SFO, then return to ORD. Treat this as a fast proof beat rather than another walkthrough.

> The same decision contract also runs on separate Atlanta and San Francisco graphs. [tail 0.4 seconds]

## 08-close.wav · factual proof to face · 2:17–2:56

**Picture:** Begin on the shared graph and agent handoffs while “shared brain” and “connects” are spoken. On “We then replayed,” flash the completed July 27 scorecard, then move to the 30-day card. Keep the exact counts visible on screen instead of reading them aloud. Animate `34.92 → 19.07 min` as the comparison is spoken, then hold it for the 0.8-second pause. At “It shows,” cut back to the same presenter framing used in the opening. End with the repository URL beside the presenter.

> FalkorDB is GroundGraph’s shared brain. [pause 0.35 seconds] It connects airport state, agent handoffs, memory, and evidence across all three tracks. [pause 0.4 seconds] We replayed thirty days at O’Hare, hour by hour, giving the forecast only the information available at that moment. [pause 0.45 seconds] Average prediction error fell from about thirty-five minutes using the schedule alone to nineteen with GroundGraph. [hold on comparison 0.8 seconds] It shows the full loop. Understand the disruption. Coordinate a safe response. Explain it and learn. [pause 0.3 seconds] That’s GroundGraph. [tail 0.5 seconds]

## Face footage

Record two silent video takes with the same framing:

1. **Opening:** at least eight seconds, looking into the lens and speaking the first sentence naturally. Continue the hook audio across the cut to the airport.
2. **Closing:** at least twelve seconds. Begin the cut on “It shows” and finish “That's GroundGraph” directly to camera. Hold eye contact for one second after the final word.

Keep the camera at eye level, use soft light from the front or 45 degrees, and leave empty space on one side for the name or repository lower third. Do not use a virtual background, AI avatar, or aggressive background blur.
