# Local setup

GroundGraph uses **Podman only** for the official-server path. It neither installs nor uses Docker Desktop or Colima.

## Prerequisites

- Python 3.12+
- Homebrew
- Podman
- Ollama with enough memory for `qwen3:4b-instruct`

Install the runtime once:

```bash
brew install python@3.12 podman ollama libomp
```

On Linux, install Python, Podman, and Ollama using their official instructions or your distribution's package manager. The remaining project commands are the same.

On macOS and Windows, Podman runs containers in a local virtual machine. Podman chooses the machine provider supported by the host and installed version; GroundGraph does not require a particular provider. On Linux, Podman runs natively and no Podman machine is normally needed. If a pre-existing Podman machine fails before FalkorDB starts, inspect it with `podman machine list`; the launcher prints the original startup error and exits rather than repeatedly modifying the machine.

Start Ollama if it is not already running:

```bash
ollama serve
```

## Clean-clone run

```bash
git clone <PUBLIC_REPOSITORY_URL> groundgraph
cd groundgraph
make setup
make pull-model
make start
```

`make start` performs the following steps:

1. initializes or starts a local Podman machine when the host requires one, or uses native Podman on Linux;
2. runs the pinned official `docker.io/falkordb/falkordb:6.0.0` image with persistent storage;
3. waits for FalkorDB to return `PONG`; and
4. launches GroundGraph at [http://127.0.0.1:5050](http://127.0.0.1:5050).

FalkorDB Browser is available at [http://127.0.0.1:3000](http://127.0.0.1:3000). Select graph `groundgraph` and use the queries in [`../cypher/`](../cypher/).

In another terminal, verify the live stack:

```bash
cd groundgraph
make verify
```

A judged server run must report all three checks:

```text
PASS GroundGraph API http://127.0.0.1:5050/api/health
PASS FalkorDB server 127.0.0.1:6379/groundgraph contains ... nodes
PASS Ollama model qwen3:4b-instruct
```

You can also inspect the runtime proof directly:

```bash
curl -s http://127.0.0.1:5050/api/health | python3 -m json.tool
```

Confirm `runtime_mode` is `server` before recording an official-server claim.

After the FalkorDB image, Python wheels, and Ollama model have been pulled once, the replay uses only checked-in data and local services.

## Non-judged development fallback and maintenance

Run without a container engine:

```bash
make start-lite
```

This uses FalkorDBLite and remains fully local. It is provided only for development when the full server cannot run; do not use it for submission screenshots, the demo video, or judged-runtime claims. On macOS, FalkorDBLite uses the `libomp` package included in the install command above.

Useful commands:

```bash
make logs      # follow official FalkorDB logs
make down      # stop the FalkorDB container
make test      # run the automated checks
make clean     # remove the container but retain its persistent volume
```

Set `GROUNDGRAPH_ENABLE_LLM=0` in `.env` to test the labeled deterministic path. Use a unique `FALKORDB_GRAPH` name if the local server contains unrelated data; GroundGraph owns and reseeds the configured graph when its scenario fingerprint changes.

The default `GROUNDGRAPH_AUTO_APPLY=1` enables policy-authorized bounded autopilot inside the replay. Set it to `0` when demonstrating supervised, click-to-apply operation.

