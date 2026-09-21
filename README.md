# laya-assist-proxy

A local-first bridge between **Home Assistant Assist** and **[laya](https://github.com/NandhaKishorM/laya)**,
a non-autoregressive decision model.

Home Assistant talks to conversation agents over an OpenAI-compatible
`/v1/chat/completions` endpoint. This project serves that endpoint, classifies the
spoken command locally with laya — one forward pass, no text generation — and
answers with a Home Assistant tool call. Only what laya is *not* confident about
is handed to a real LLM.

```
"Mach das Licht in der Küche an"

  Home Assistant  ──sentence + intent tools──▶  laya-assist-proxy
                                                      │
                                                      ├─ laya, ~200 ms, local
                                                      │  action=an  device=kueche_licht  area=kueche
                                                      │
                  ◀──── HassTurnOn {"name": "Küche Licht", "domain": ["light"]}
```

The premise: most voice commands are closed-set classification, not generation.
Which of my 40 devices, which of 8 actions, which room — that is a decision
problem, and routing it through a frontier model costs latency, money and
privacy for no gain. Laya answers typed questions (`choice`, `score`, `noul`) in
a single forward pass and returns calibrated confidence, so there is a principled
place to draw the line: above the threshold it answers, below it something else
does.

**Status:** works, and is used daily against a real Home Assistant instance. The
device-matching quality depends heavily on how your entities are named — see
[Known limitations](#known-limitations).

## How a request flows

1. Home Assistant sends the sentence plus its intent tools (`HassTurnOn`,
   `HassLightSet`, …) in the `tools` field.
2. laya answers seven typed questions about the sentence in one pass: action,
   device, area, brightness, temperature, is-it-a-question, needs-confirmation.
3. The answers become a Home Assistant tool call — built only from tools the
   client actually offered, and only with parameters in that tool's JSON schema,
   so the mapping survives Home Assistant renaming things between versions.
4. Below the confidence threshold, or when the action has no intent counterpart,
   the **unchanged** request goes to the fallback LLM (any OpenAI-compatible
   endpoint; Ollama local or cloud).
5. Home Assistant executes the tool and sends the result back for a sentence to
   speak. After a switching command that is a fixed acknowledgement, with no
   round trip. After a status question the fallback phrases the answer — the
   answer is *in* that tool result, and a canned "done" would swallow it.

Every decision is visible in the UI and at `GET /api/trace`: which route was
taken, why, which tool was called with which arguments, and how long it took.

## The entity catalog

The catalog is what laya chooses between, and building it correctly is most of
the work.

**Only entities exposed to Assist.** That flag lives in
`.storage/homeassistant.exposed_entities` and is not in the REST API, so the
proxy reads it over the WebSocket API (`homeassistant/expose_entity/list`,
assistant `conversation`). Areas are not restricted — all areas holding an
exposed entity come along.

**Voice aliases come along too.** `config/entity_registry/list` does not return
them; `config/entity_registry/get_entries` does. Aliases are folded into the
criteria text laya matches against, which is usually the single best thing you
can do for hit rate:

```
garten_garten  ->  "Garten Garten, Gartenlicht im Bereich Garten"
```

**Every name is verified against Home Assistant's own matcher.** A tool call
names its target by `name`, and Home Assistant resolves it. Not every display
name resolves: duplicates are ambiguous, and some names the intent matcher
simply does not know, even for a unique, exposed entity. Home Assistant still
reports such a tool call as executed — **nothing happens, and nobody is told.**
So at catalog build time each name is probed once with the read-only
`HassGetState` intent, and the first name that actually resolves is stored as
`assist_name` and used in tool calls:

```
Power Bambulab  ->  no match          Bambulab (alias)  ->  switch.buero_bambulab  ✓
```

Entities that resolve under no name at all are reported by name in the UI —
there, an alias in Home Assistant is the only fix.

**Areas are real areas**, resolved by Home Assistant itself through a
`/api/template` call using `area_name()`, not guessed from the display name.

Supported domains: `light`, `switch`, `climate`, `cover`, `media_player`,
`lock`, `vacuum`, `fan`, `scene`, `script`.

## Alternative decision engine: kev

[kev](https://github.com/jaredpalmer/kev) is a family of small decision models
on Qwen, and it answers the *same* typed questions as laya — `choice`, `score`
and `noul`, with probabilities instead of a label. So the question schema in
`schema.py` drops into it unchanged, and the proxy can use either engine.

The difference is the shape: laya runs in-process, kev runs as its own server.

```sh
git clone https://github.com/jaredpalmer/kev.git && cd kev
uv sync --extra serve
KEV_DTYPE=bf16 uv run --extra serve python -m kev.serve --run jaredpalmer/kev-4b --port 8009
```

Then switch in the UI (*Entscheider*), or:

```sh
curl -X POST localhost:7788/api/engine -H 'content-type: application/json' \
     -d '{"engine": "kev", "kev_url": "http://localhost:8009"}'
```

```yaml
engine:
  name: laya            # or kev
  url: http://localhost:8009
  model: kev-latest
```

Switching to kev is refused while kev does not answer, so a typo in the URL
cannot silently break voice control. The trace labels each decision with the
engine that made it.

Worth knowing before expecting a win: kev's models are 0.8B to 9B, orders of
magnitude larger than laya's, and kev's own README notes that Apple Silicon is
"currently slow without optimized kernels" and that its "probabilities aren't
well calibrated on new sources". Calibration is what the confidence threshold
here is built on, so the threshold likely needs re-tuning for kev. `POST
/api/bench` measures both under the same questions.

## Conversation context

By default laya sees the current command plus the **last 2 utterances** from the
Home Assistant conversation, so pronouns resolve:

```yaml
context:
  entries: 2          # 0 = off, maximum 8
```

Entries are counted as utterances, not turns — a question and its answer are
two. Each is truncated to 120 characters, tool and system messages are left out.
The budget is tight on purpose: laya has roughly 320 tokens for state, and the
state is truncated at the end, so `command` comes first and context is what
falls off, never the command.

**Not every question sees the context.** Measured on *"turn the bambu on"* →
*"turn it off again"*:

| question | without context | with context |
|---|---|---|
| `device` | wrong entity, 0.97 | correct entity, 1.00 |
| `action` | `off`, 0.66 — correct | `on`, 0.43 — **wrong** |

The device question needs the history to resolve the pronoun; the action
question is dragged by it, because the previous turn contains the opposite
action. Rewording the instruction did not fix that, so the questions run in two
groups, with and without context. It costs nothing measurable — the number of
sequences is identical, only the batch is split.

## Setup

Requirements: Python 3.9+, and a Home Assistant long-lived access token from an
**admin** user — the registry commands behind the Assist filter need one.

```sh
python -m venv .venv && . .venv/bin/activate
pip install -e .          # pulls in laya and PyYAML
laya-assist               # or: python -m laya_assist
```

The UI and the API are on **http://localhost:7788**. The first start downloads
the laya checkpoint and takes a few minutes; `GET /api/health` reports
`"loading": true` until it is ready.

Configuration is read from the **working directory** (`project.yaml`,
`secrets.local.json`), so start it from the project directory or set
`LAYA_ASSIST_HOME`.

In the UI, enter the Home Assistant URL and token and press *Katalog laden*.

### Pointing Home Assistant here

Home Assistant's bundled OpenAI Conversation integration has no field for a base
URL — it talks to api.openai.com only. Use an integration that allows one, e.g.
*Extended OpenAI Conversation* or *OpenAI Compatible Conversation* from HACS:

| field | value |
|---|---|
| Base URL | `http://<host>:7788/v1` |
| API key | anything, it is not checked |
| Model | `laya` |

Then create a pipeline under *Settings → Voice assistants* and select that agent.

## Configuration

`project.yaml` holds everything the UI manages — model, threshold, fallback,
context size, Home Assistant URL and the generated catalog. It is written on
every change and is **gitignored**, because it is machine-specific and carries
your entity list. `project.example.yaml` shows the full shape:

```yaml
model:
  default: multilingual
  threshold: 0.6
fallback:
  enabled: true
  url: http://localhost:11434
  model: qwen3:8b
context:
  entries: 2
homeassistant:
  url: http://homeassistant.local:8123
  save_token: true
catalog:
  areas: {...}        # written by "Katalog laden"
  entities: {...}
```

The token is never in there. It lives in `secrets.local.json` next to it
(gitignored), and only if *Token speichern* is checked.

## API

| endpoint | purpose |
|---|---|
| `GET /api/health` | model, device, catalog status |
| `GET/POST /api/schema` | current catalog and generated questions |
| `POST /api/predict` | `{"text", "context"}` → answers, toolcall, timing |
| `POST /api/bench` | latency percentiles |
| `POST /api/ha/connect` | build the catalog from Home Assistant |
| `POST /api/ha/execute` | run a tool call against Home Assistant |
| `GET/POST /api/context` | how many utterances laya sees |
| `GET/POST /api/fallback` | fallback LLM settings |
| `GET /api/trace` | the last 25 decisions, with reasons |
| `GET /v1/models`, `POST /v1/chat/completions` | the OpenAI-compatible surface |

## Running as a service

`service/` has a launchd agent for macOS that starts the proxy at login and
restarts it if it dies.

The setup it is written for: **edit on a network share, run from a local copy.**
`service/deploy.sh` copies the working state onto the device and restarts the
service — without touching `project.yaml` or `secrets.local.json` there, which
belong to the device and not to the working copy.

```sh
./service/deploy.sh
```

That is not a detour, it is the fix for two constraints: launchd may not execute
a file from a network share, and at login time the share is not mounted yet.
Running locally makes the service independent of both. See
[service/README.md](service/README.md).

## Layout

```
laya_assist/
    server.py      HTTP server: web UI, JSON API, /v1 endpoint, the laya engine holder
    schema.py      the typed questions laya answers, and the toolcall built from them
    ha.py          Home Assistant REST: catalog, service calls, name verification
    ha_ws.py       Home Assistant WebSocket: Assist exposure and voice aliases
    oai.py         the OpenAI-compatible layer: tool calls, context, SSE
    ollama.py      fallback LLM client
    settings.py    project.yaml and secrets.local.json
    static/        the web UI, one file
service/           launchd agent for macOS
```

Standard library only, apart from laya itself and PyYAML — no web framework.

## Known limitations

**Device matching is only as good as your entity names.** Home Assistant display
names like `Küche Licht Küche`, `Garten Garten` or `shellyplus1pm-wohnzimmer`
are hard to tell apart, and laya will confidently pick the wrong one. Aliases fix
this better than anything else — they land both in the matching criteria and in
the tool call.

**`score` questions are unreliable.** Brightness and temperature come from
five-level score questions and do not hit numbers: "at 25 percent" yields 50%.
Worse for temperature: *"it is too cold in the bedroom"* yields 16 °C — the model
describes the current state rather than the wish. Explicit numbers should be
pulled out of the sentence deterministically instead.

**The confidence threshold is a hard edge**, and it runs through the middle of
ordinary sentences. That is intended — the fallback exists for the other side of
it — but expect to tune it.

## Credits

The decision engine is [laya](https://github.com/NandhaKishorM/laya) by Convai
Innovations, used as a dependency (`pip install laya`) — the model, its
checkpoints and the calibration work are theirs. This project is the Home
Assistant side: the conversation-agent endpoint, the entity catalog, the tool-call
mapping and the web UI.

Apache-2.0, like laya. See [LICENSE](LICENSE).
