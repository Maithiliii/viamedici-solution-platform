# Solution Enablement Platform (SEP)

A platform that helps Viamedici solution engineers solve new customer cases with the knowledge from past cases. AI agents analyse a new case, search the case history, check whether earlier solutions actually apply to this customer and version, and suggest what to investigate next. The engineer decides what to use. When the case is solved, the platform turns it into reusable knowledge.

Built for the Viamedici x Monteiro Brothers engineering assessment.

**Stack:** Directus 11, PostgreSQL 16 (pgvector, full text search, pg_trgm), Python (FastAPI, LangGraph), Gemini, Ollama embeddings, Docker Compose.

## Documentation

| Document | Contents |
|---|---|
| [01 Research](docs/01-research.md) | Case management tools, KCS, ITIL, case-based reasoning, search, RAG, agent architectures, the PIM domain, design decisions |
| [02 Product & Implementation](docs/02-product-implementation.md) | Product concept, user flows, scope (Must / Should / Future), architecture, ERD, search design, agent design, sequence diagrams, technology decisions, plan |
| [03 Technical documentation](docs/03-technical.md) | How the code is organised, how each agent graph works, API, data flow, configuration, known limitations |

## The idea in one flow

```
New case (Directus) -> Flow -> Case Analysis agents -> suggestions (Directus)
     -> engineer accepts / rejects, logs investigation steps
     -> resolves case -> Knowledge Capture agent -> architect approves
     -> solution created/updated, track record updated, search index updated
```

What the engineer gets for a new case:

- **Have we seen this before?** similar cases and solutions from hybrid search (vector + full text + error codes)
- **Does the old solution apply here?** a verdict per solution, based on version range, modules, integrations, track record per version and what happened at this customer before
- **What changed?** if the customer upgraded, the release notes between the two versions are searched as well
- **What next?** hypotheses, next investigation steps and the questions to ask the customer
- **Ask the case** free questions answered from the case history, with sources

## Running it

### Requirements

- Docker Desktop
- [Ollama](https://ollama.com) running on the host (used for embeddings)
- A free Gemini API key from https://aistudio.google.com/apikey
- Node.js 20+ only if you want to change the Directus extension (the built version is included)

### Steps

```bash
cp .env.example .env
# fill in GEMINI_API_KEY and generate the secrets (see comments in the file)

ollama pull nomic-embed-text

docker compose up -d postgres directus agent-service
docker compose run --rm setup
```

`setup` configures Directus (collections, relations, roles, policies, users, Flows, dashboard), loads the dataset, creates the embeddings and builds the search index. It can be run again safely.

Open http://localhost:8055 and log in.

| User | Role | Password |
|---|---|---|
| lena.hoffmann@sep.example.com | Solution Architect (can approve knowledge) | `SepDemo!2026` |
| jonas.weber@sep.example.com | Support Engineer | `SepDemo!2026` |
| admin@example.com | Administrator | from `.env` |

The **Engineer Workspace** is the first icon in the module bar.

To start again from an empty database: `docker compose down -v` and repeat the steps above.

## Demo

The demo cases are in `data/dataset/demo_cases.yaml`. They are not seeded, create them in the workspace with **+**.

**1. The example from the brief** (customer Rheinwerk Tools GmbH, EPIM 9.1)

> Our product information is not appearing correctly on the webshop after an update. Since the weekend new products and price changes coming from SAP are not visible in the Shopware shop... We already tried a full export like last time, it did not change anything.

Expected: the agent sees that Rheinwerk upgraded from 9.0 to 9.1, finds the breaking change in workflow conditions and ranks **SOL-016** first, with **SOL-003** (export only takes released products) as the related cause. **SOL-001** (full export) is marked not applicable: it is for 8.x and already failed for this customer on 9.0.

Then: add a next step to the log, record the finding, reference SOL-016, resolve the case and approve the knowledge suggestion. SOL-016's track record goes up by one.

**2. No known solution** (customer Polar, export stuck at 99%). There is no validated solution, so the agent should say so instead of forcing one. Under **Problems**, run problem detection: it groups the open 9.2 cases with the same symptom and proposes a problem record.

**3. Simple reuse** (customer Hanse, broken umlauts after supplier import) leads to SOL-006 with a strong track record.

A demo case can also be created from the command line:

```bash
docker compose exec agent-service python -m sep.cli.demo demo-1
```

## Repository layout

```
agent-service/        Python service: agents, search, indexing, setup scripts
  sep/agents/         intake, retrieval (tool-using agent), applicability, planner, capture, ask
  sep/graphs/         LangGraph graphs: case analysis, investigation, knowledge capture, problem detection
  sep/llm/            provider layer (Gemini, OpenAI-compatible), model fallback
  sep/cli/            setup, Directus bootstrap, seed, demo
db/init/              SQL schema, search schema and hybrid search function
directus/extensions/  Directus bundle: Engineer Workspace module + /sep endpoint
data/dataset/         sample dataset (taxonomy, solutions with their cases, open cases, demo cases)
docs/                 research, product & implementation, technical docs
eval/                 held-out cases for retrieval evaluation
```
