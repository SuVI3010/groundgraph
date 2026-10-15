# AI use disclosure

GroundGraph uses AI in two distinct roles.

## Product runtime

The local coordinator uses `qwen3:4b-instruct` through Ollama. It receives a bounded list of candidates that deterministic application code has already generated from FalkorDB state and policy evidence. The model may select one supplied candidate ID and summarize its reason. It cannot create arbitrary flight IDs, resources, policies, or executable actions.

The server verifies the returned ID. An unavailable model, invalid JSON, timeout, or unknown ID activates a labeled deterministic fallback. Every application path performs a fresh graph-version, replay-constraint, current-policy, and current-knowledge check. The default bounded-autopilot mode may apply an in-scope plan under the synthetic `APPLY_VALIDATED_PLAN` policy; setting `GROUNDGRAPH_AUTO_APPLY=0` requires human approval. In this project, “validation” means consistency and authorization checks inside the bounded replay; it is not aviation safety certification.

## Development assistance

OpenAI Codex assisted with implementation, tests, interface work, documentation, and release packaging. The team chose the problem and scope, verified public-source boundaries, reviewed the architecture and code, ran the application, and is responsible for the submission.

No generated output is presented as measured real-world airport impact. The project does not make operational aviation decisions or control any airport system.

## Dependency and tool disclosure

The submission uses these external runtime and development components:

| Component | Version or image | Role |
|---|---|---|
| [FalkorDB server](https://github.com/FalkorDB/FalkorDB) | `docker.io/falkordb/falkordb:6.0.0` | Judged property-graph runtime and FalkorDB Browser. |
| [FalkorDB Python client](https://pypi.org/project/FalkorDB/) | `1.7.1` | Application access to the official server. |
| [FalkorDBLite](https://github.com/FalkorDB/falkordblite) | `0.10.0` | Non-judged development fallback; not used in submission evidence. |
| [Flask](https://flask.palletsprojects.com/) | `3.1.3` | Local HTTP API and static interface. |
| [Ollama](https://ollama.com/) | locally installed | Local model runtime. |
| [Qwen3 4B Instruct](https://ollama.com/library/qwen3:4b-instruct) | `qwen3:4b-instruct` | Bounded candidate selection and explanation. |
| [Podman](https://podman.io/) | locally installed | Sole container runtime for the official FalkorDB image. |
| [pytest](https://pytest.org/) | `9.1.1` | Development verification only. |

The browser interface uses plain HTML, CSS, Canvas, and JavaScript; it does not load a hosted UI framework or analytics service. Public-data dependencies and their exact decision-time boundaries are documented in [Data provenance](data-provenance.md).

