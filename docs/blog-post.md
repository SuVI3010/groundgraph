---
layout: default
title: "We Didn’t Build Another Coding Assistant"
description: "How one airport delay led us to a graph-native, multi-agent recovery framework."
---

# We Didn’t Build Another Coding Assistant

*We followed one airport delay instead—and found a problem built for graphs.*

*By Viswesh Suri and Sai Pranav Siddamshetty · Graph Hacks 2026*

[![GroundGraph showing a connected airport recovery](https://raw.githubusercontent.com/SuVI3010/groundgraph/main/demo/groundgraph-demo-cover.jpg)](https://youtu.be/Cc0sYNm7V-0)

**[Watch the three-minute working demo](https://youtu.be/Cc0sYNm7V-0).**

At the start of Graph Hacks, the safest idea was obvious: build another assistant for developers.

It could read a repository, answer questions, write documentation, or help organize software work. Useful ideas—but familiar ones. We wanted to take agents outside the software world and place them somewhere that context has consequences.

So we asked a different question:

**Where do relationships matter as much as the data itself?**

That question led us to an airport.

## One delay is never one delay

An airport delay rarely stays in one place.

A baggage problem can hold a departure. The aircraft remains at its gate. The delayed gate affects another arrival. Turnaround work shifts. The aircraft’s next flight inherits the problem. Add poor weather or reduced runway capacity, and one local disruption becomes a network of consequences.

Spotting the delay is easy. The difficult part is understanding everything connected to it, choosing a safe response, explaining that choice, and remembering the result.

That became **GroundGraph**.

GroundGraph is a research framework for airport operations recovery. It does not issue aircraft clearances or connect to live airport systems. It replays operational scenarios and lets multiple specialist agents investigate a disruption, coordinate a response, and continuously decide whether intervention is worthwhile.

We began with Chicago O’Hare and later packaged separate Atlanta and San Francisco graphs to test the same decision process across different airport contexts.

## Six specialists, one shared picture

Consider a baggage delay appearing at 08:36.

Six agents examine the same version of the airport graph:

- **Weather** checks operating conditions.
- **Flow** studies runway demand and capacity.
- **Gate** checks compatible resources.
- **Turnaround** follows the delayed task chain.
- **Policy** verifies which actions are authorized.
- **Memory** finds relevant earlier outcomes.

Their coordination is more structured than a group chat between agents. Each specialist writes a finding and handoff into FalkorDB, connected to the disruption, supporting evidence, and exact graph version it examined.

Only after all six handoffs arrive does the coordinator build possible recovery plans.

A local Qwen model helps choose among those plans, but its authority is deliberately narrow. Deterministic rules and graph relationships construct the valid shortlist first. The model can select an allowed option; it cannot invent a new action, rewrite policy, or execute a plan by itself.

Before a modeled action is applied, GroundGraph runs seven fresh checks against FalkorDB. If a gate changed, a policy was updated, evidence is missing, or the plan has become stale, the action is rejected. Continuous replanning runs every five replay minutes and whenever an important event changes the graph.

Sometimes GroundGraph acts. Sometimes it decides the expected benefit is too small and leaves the operation alone.

That restraint is part of the decision.

## Why FalkorDB made the idea work

FalkorDB is much more than GroundGraph’s storage layer. It is the shared state through which the agents coordinate, the policy authority they must obey, the memory they can consult, and the evidence trail behind every decision.

That architecture fits airport operations naturally.

A disruption affects a flight. A flight occupies a gate, requires turnaround tasks, and rotates into another flight. An action uses resources and must be permitted by a policy. These are relationships first, so representing them as a graph keeps the operational chain intact.

FalkorDB also gives every handoff a clear origin. We can inspect what each agent knew, which evidence the coordinator used, what choices reached Qwen, and why the selected response passed its guards. If FalkorDB becomes unavailable, GroundGraph freezes the replay and disables operational controls. Coordination and authorization cannot quietly continue from an unverified copy.

The same graph supports GroundGraph’s **Company Brain**. An operator can ask who owns gate recovery or which procedure is current. The answer follows connected policies, procedure versions, documents, teams, and roles, then shows the supporting evidence. When the graph cannot support an answer, the system says that evidence is insufficient.

FalkorDB gave our agents something more valuable than a longer prompt: a shared, inspectable understanding of the situation.

## Testing against what actually happened

Modeled recovery scenarios show how GroundGraph reasons, but they cannot prove real-world impact. We therefore built a separate retrospective benchmark using public O’Hare movement and weather data.

At each hourly cutoff, GroundGraph receives only information that would have been available at that moment. It makes a forecast for the next hour, saves it, and reveals the recorded result afterward.

Across all 30 reconstructable days from July 2 through July 31, the benchmark scored **638 hourly forecasts covering 54,774 target movements**. The schedule-only baseline produced an average hourly delay error of **34.92 minutes**. GroundGraph’s frozen, June-trained historical analogue reduced that error to **19.07 minutes**.

We treat that result carefully. It is a one-airport retrospective forecast benchmark. It does not claim that GroundGraph operated an airport or saved real-world delay minutes. The month-long evaluator runs without the graph for speed; a separate selected-day FalkorDB replay proves the six-agent handoffs and evidence trail. Together, they show a strict time boundary and a forecast that tracks recorded conditions better than the schedule alone.

## What we built across three tracks

GroundGraph brings the three Graph Hacks tracks into one workflow.

Its agents act on connected airport data. They coordinate through versioned findings and handoffs, then reuse outcomes only when the new context matches. Its Company Brain connects operational questions to policies, ownership, procedures, and source evidence.

We began by looking beyond another software assistant. We ended with a graph-native framework in which agents can understand a disruption, coordinate a bounded response, explain it, and learn from the outcome.

Airports run on relationships. With FalkorDB, GroundGraph can reason through them.

**[Explore the source code, graph model, and reproducible benchmark](https://github.com/SuVI3010/groundgraph).**
