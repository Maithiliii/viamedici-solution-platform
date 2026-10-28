# 03 Technical documentation

How the prototype is built and how the parts fit together. Why it is designed this way is in [02 Product & Implementation](02-product-implementation.md).

## 1. Components

| Component | Technology | Responsibility |
|---|---|---|
| PostgreSQL 16 | `pgvector/pgvector:pg16` | All business data (cases, solutions, customers, releases...), agent runs and steps, AI suggestions, and the search index in a separate `search` schema |
| Directus 11.17 | `directus/directus:11.17.4` | Data studio and REST API on top of the schema, roles and policies, Flows (automation), Insights dashboard, and the SEP extension |
| SEP extension | Directus bundle (Vue 3 module + endpoint) | Engineer Workspace UI; `/sep/*` endpoint that proxies to the agent service and applies accepted suggestions |
| Agent service | Python 3.12, FastAPI, LangGraph | Agent graphs, hybrid search, indexing, setup scripts |
| LLM | Gemini (free tier) | Reasoning, structured output, tool calling |
| Embeddings | Ollama `nomic-embed-text` (768 dimensions), runs on the host | Embeddings for the search index and queries |

All services run with `docker compose`. The agent service is only reachable from Directus (shared secret in the `X-SEP-Secret` header); the browser only ever talks to Directus.

## 2. Data model

The full ERD is in doc 02, section 5. Schema: `db/init/02_schema.sql`.

The main points:

- Case and Solution are separate entities. A case is one customer problem; a solution is reusable knowledge (KCS article: issue, environment, cause, resolution). `case_solutions` connects them and stores what happened: `relation` (applied / referenced / derived_from), `outcome` (resolved / partial / failed / not_tried) and the EPIM version at the time.
- Track record is computed by the database. A trigger on `case_solutions` updates `times_applied`, `times_successful` and `times_failed` on the solution. The view `solution_version_track_record` breaks this down per major version, which is how the agent knows that SOL-001 worked on 8.x but failed on 9.x.
- The customer environment is structured data: customer -> environment -> release (version), licensed modules, integrations (with version). Cases store `epim_version` at the time of the case, because environments get upgraded.
- Releases and release changes (feature / fix / breaking / deprecation, per module) let the agents reason about upgrades.
- Investigation steps record what was checked, what was found and whether it confirmed or ruled out a hypothesis.
- Agents never write business data. They write `ai_suggestions` (type, payload, rationale, confidence, citations). `agent_runs` and `agent_steps` are the audit trail of every run.
- Problems (ITIL) group cases with a shared underlying cause.

User foreign keys (`assigned_to`, `author`, `approved_by`...) are created by the bootstrap through the Directus API, because `directus_users` only exists after Directus has started.

## 3. Search

Schema: `db/init/03_search.sql`. Code: `agent-service/sep/indexer.py`, `sep/search.py`.

Index. Each case and solution is split into chunks in `search.knowledge_chunks`:

| Entity | Chunk | Content |
|---|---|---|
| case | problem | title, customer report, symptoms, error messages |
| case | resolution | root cause, resolution summary, confirmed investigation findings (resolved cases only) |
| solution | issue | title, issue, environment conditions |
| solution | fix | root cause, cause, resolution steps |

Each chunk has an embedding, a generated `tsvector`, the extracted error codes and metadata (modules, integrations, version, track record) used for boosting.

Query. `search.hybrid_search(...)` runs three retrievers and fuses them:

1. vector similarity (HNSW, cosine)
2. full text search (OR query over the tokens, so long reports still match)
3. trigram match on error codes such as `WF-COND-301` or `API-AUTH-410`

The ranked lists are combined with Reciprocal Rank Fusion (k = 60) per entity, then multiplied by `1 + boost`:

| Boost | Value |
|---|---|
| same module | +0.30 |
| shared integration | +0.20 |
| version fits (same major for cases, inside the valid range for solutions) | +0.10 |
| track record (share of successful applications) | up to +0.20 |

The result includes a breakdown (rank in each retriever and each boost) so the UI and the agents can see why something matched.

Cases and solutions are re-indexed automatically: the Directus Flow on case updates calls the agent service, and approving knowledge re-indexes the solution.

## 4. Agents

Code: `agent-service/sep/agents/` and `sep/graphs/`.

The order of steps is a fixed LangGraph graph, which keeps it predictable and easy to test. Only retrieval, where the path really varies, is a tool-using agent.

### 4.1 Case Analysis graph (runs when a case is created)

```
load -> intake -> retrieval -> applicability -> planner -> write
```

| Node | Type | What it does |
|---|---|---|
| load | code | Loads the case with customer, environment, version, modules, integrations and investigation steps through Directus |
| intake | LLM, structured output | Turns the customer text into a structured case: summary, module, integrations, symptoms, error codes, customer goal, suspected trigger, severity, missing information and a message to the customer |
| retrieval | tool-using agent (ReAct) | Starts from a hybrid search. If the customer recently upgraded, it also loads the breaking changes between the two versions and searches with them. Then it explores with tools: `search_knowledge`, `get_solution`, `get_case`, `get_customer_history`, `get_release_changes`, and calls `finish` with ranked candidates |
| applicability | code + LLM | Code collects facts per candidate solution (valid versions, module and integration overlap, track record per version, release changes since the solution was last proven, history at this customer). The LLM gives a verdict: applies / applies with changes / not applicable / unknown, with a reason |
| planner | LLM, structured output | Hypotheses with how to confirm them, and concrete next steps, each citing the sources it is based on |
| write | code | Writes the suggestions to Directus and marks older pending ones as superseded |

### 4.2 Other graphs

| Graph | Trigger | What it does |
|---|---|---|
| Investigation guide | "Suggest next steps" button | Reads the investigation log (what was confirmed, what was ruled out), does more research and re-plans the next steps |
| Knowledge capture | Case status becomes `resolved` (Flow) | Compares the resolved case with existing solutions and proposes to link (an existing solution fits as is), update (it needs an extension) or create (new KCS article draft). An architect reviews and approves it |
| Problem detection | Nightly Flow (03:00) or the Problems page | Finds open cases without a solution, pairs them by embedding similarity (cosine >= 0.72), builds clusters of 3+ cases and lets the LLM decide whether they share a cause. Proposes problem records |
| Ask | "Ask about this case" | Answers a free question about the case using search and case history, with sources |

### 4.3 LLM layer

`sep/llm/` hides the provider behind one interface (chat with tools, structured output).

- Gemini is called through its REST API with native function calling.
- `GEMINI_MODELS` is a list of models. When a model is overloaded (HTTP 503 or 429) the router retries and then falls back to the next one. An OpenAI-compatible provider (Groq) can be added to the chain.
- Structured outputs are validated with Pydantic; invalid output is sent back to the model with the validation error and retried.

### 4.4 Guardrails

- The customer text is untrusted. It is fenced and the prompts tell the model to treat it as data. Common prompt-injection patterns are detected and logged as a guardrail step.
- E-mail addresses and phone numbers are masked before text is sent to the LLM.
- Every candidate and citation must be a case or solution number that was actually retrieved in this run (`RetrievalLedger`). Anything else is dropped and logged.
- If no solution fits, the agent says so instead of forcing a match.
- If the retrieval agent fails, the node falls back to the plain hybrid search ranking, so the engineer still gets results.

### 4.5 Human in the loop

The agent service uses its own Directus user (role "AI Agent") whose policy only allows reading, creating suggestions and changing the case status. Applying a suggestion happens in the SEP endpoint with the logged-in user's permissions, so Directus decides who may do what. For example, a Support Engineer cannot publish a solution; a Solution Architect can.

| Suggestion | What accepting it does |
|---|---|
| case_structure | Sets module, severity, symptoms, error messages, goal on the case |
| clarification | Sets the case to "waiting for customer" and stores the questions |
| next_step / hypothesis | Adds a step to the investigation log |
| solution | Links the solution to the case as referenced |
| similar_case | Creates a case link |
| knowledge_link / update / create | Records the solution as applied and resolved, updates or creates the solution, closes the case, re-indexes |
| problem | Creates a problem and links the cases |

Rejections are stored with an optional note.

## 5. Directus configuration

`sep/cli/bootstrap_directus.py` creates all of this, so the setup is the same every time:

- collection and field metadata (folders, icons, display templates, interfaces, relations including O2M and M2M aliases)
- policies and roles: Support Engineer, Solution Architect, AI Agent
- users (demo team and the agent service account with a static token)
- Flows: "Analyse new case" (items.create on cases), "Case updated" (items.update), "Nightly problem detection" (schedule). They call the agent service with the secret from the environment (`FLOWS_ENV_ALLOW_LIST`)
- the "Solution Enablement" Insights dashboard
- project settings and module bar (Engineer Workspace first)

## 6. Agent service API

All endpoints except `/health` require the `X-SEP-Secret` header.

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Health check |
| POST | `/runs/case-analysis` | Start case analysis (`{case_id, wait?}`) |
| POST | `/runs/investigation` | Start the investigation guide |
| POST | `/runs/knowledge-capture` | Start knowledge capture |
| POST | `/runs/problem-detection` | Start problem detection |
| POST | `/ask` | Answer a question about a case |
| POST | `/flows/case-event` | Webhook for the Directus Flows on cases |
| POST | `/index/{case or solution}/{id}` | Re-index one entity |
| POST | `/index/rebuild` | Rebuild the whole index |
| GET | `/search` | Hybrid search |

Runs started without `wait` run in a thread pool; the same run for the same case is not started twice at the same time.

The SEP endpoint in Directus (`/sep/...`) exposes the runs, search and ask for logged-in users, plus `POST /sep/suggestions/:id/decide` and `POST /sep/cases/:id/resolve`.

## 7. Dataset

`data/dataset/` contains an invented but realistic EPIM support history:

- `taxonomy.yaml`: team, 12 modules, 13 integrations, releases 8.0 to 9.2 with their changes, root causes, 12 customers with version, modules, integrations and configuration notes
- `knowledge_*.yaml`: 27 solutions, each with the cases where it was used (78 cases in total), including investigation steps, outcomes and solutions that were tried and failed
- `open_cases.yaml`: 5 unsolved cases on 9.2 that share a hidden cause (for problem detection)
- `demo_cases.yaml`: 3 demo scenarios (not loaded)

18 cases are marked as held out (`eval/holdout.json`) for evaluating retrieval.

The dataset contains deliberate traps: solutions that look similar but are only valid for older versions, solutions that failed for a specific customer, and error codes that look alike (`API-AUTH-401` vs `API-AUTH-410`).

## 8. Configuration

All configuration is in `.env` (template: `.env.example`).

| Variable | Meaning |
|---|---|
| `GEMINI_API_KEY`, `GEMINI_MODELS` | LLM key and model fallback chain |
| `GROQ_API_KEY` | Optional second provider |
| `EMBEDDING_MODEL`, `EMBEDDING_DIM`, `OLLAMA_BASE_URL` | Embeddings |
| `POSTGRES_*` | Database |
| `DIRECTUS_KEY`, `DIRECTUS_SECRET`, `DIRECTUS_ADMIN_*` | Directus |
| `DIRECTUS_AGENT_TOKEN` | Static token of the agent service user |
| `AGENT_SERVICE_SECRET` | Shared secret between Directus and the agent service |

## 9. Developing

- The agent code is mounted into the containers (`./agent-service/sep`), so a change only needs `docker compose restart agent-service`.
- The extension is built with `npm install` and `npm run build` in `directus/extensions/directus-extension-sep`; Directus reloads it automatically.
- Useful commands:

```bash
docker compose logs -f agent-service
docker compose exec agent-service python -m sep.cli.demo demo-1
docker compose exec agent-service python -m sep.cli.seed --force
docker compose exec postgres psql -U directus -d solution_platform
```

## 10. Known limitations and decisions made during the build

- Directus 11 instead of 12. Directus 12 without a licence limits the number of users and does not allow field-level permissions, which the role model needs. Directus 11.17 has neither restriction.
- Free Gemini tier. Models are sometimes overloaded; the fallback chain handles this but a run can take 30 to 90 seconds.
- Polling instead of streaming for the live trace (see doc 02, technology decisions).
- Embeddings run on the host through Ollama to keep the containers small; a GPU is not required.
- Not built yet: the automated retrieval evaluation on the held-out cases, attachment and log file analysis, e-mail intake, and SSO.
