# 01 · Research & Product Thinking

> Goal of this document: understand the problem space before designing anything, and derive concrete design decisions from what already exists.
> Every section ends with **→ Implication**, the design decision it leads to.

---

## 1. Problem framing

Viamedici's solution experts accumulate knowledge every time they solve a customer case. Today that knowledge is spread across:

- ticket histories (unstructured, written for the moment, not for reuse)
- individual engineers' heads (tacit knowledge, lost when people move on)
- documents / wikis (structured, but disconnected from the cases that produced them)

The cost is **re-investigation**: a new case is investigated from zero even though a similar case was solved before. The hard part is not *storing* history. It is **retrieving the right history at the right moment and judging whether it applies**.

A previous solution is only useful if:
1. it can be **found** even when the new case is described in different words,
2. the engineer can see **evidence** that it worked (and where it did not),
3. the engineer can judge **applicability** (same module? same version? same integration?),
4. new solutions flow **back** into the knowledge base with low effort.

**→ Implication:** the product is not a ticket system. It is a *knowledge reuse loop* attached to case work. Success = less time to resolution, more reused solutions, and a knowledge base that improves with every case.

---

## 2. Existing enterprise case / ticket management systems

| Product | What it does well | Relevant AI / knowledge features | Gap for our problem |
|---|---|---|---|
| **ServiceNow ITSM / CSM** | Strict ITIL processes: Incident → Problem → Known Error; strong workflow engine | Similar-incident suggestions, knowledge article recommendations, generative case summaries | Knowledge articles are a separate silo; "did this fix actually work, and in which environment" is weakly modelled |
| **Jira Service Management** | Developer-friendly, links tickets to engineering issues; Confluence as KB | AI-suggested articles, ticket summarisation, related-issue grouping | KB lives in Confluence pages: unstructured, no outcome tracking per solution |
| **Zendesk** | Fast agent UX, macros, help center | AI agents for deflection, intent detection, suggested macros | Optimised for high-volume B2C deflection, not deep technical investigation |
| **Salesforce Service Cloud** | Case object tightly integrated with CRM/customer data; Knowledge object | Case classification, article recommendations, reply generation | Strong on customer context, weak on technical environment and investigation steps |

**Common patterns worth adopting**
- Case ↔ knowledge article linking ("attach article to case")
- Suggested articles shown *while* working the case, not in a separate search tool
- Status workflows and role-based permissions
- Audit trail of every change

**Common weaknesses we can address**
1. **Ticket-centric, not solution-centric.** The unit of value is the ticket; knowledge is a by-product.
2. **No outcome evidence.** Few systems track "this solution was applied 8 times, succeeded 7, failed once on version X".
3. **No environment-aware applicability.** Similarity is computed on text alone, ignoring version / module / integration.
4. **The investigation is lost.** Systems keep the final answer but not the path (what was checked, what was ruled out). For a hard technical problem, the ruled-out hypotheses are often the most valuable part.

**→ Implication:** model **Cases** and **Solutions** as separate first-class entities connected by an **outcome-bearing relation**. Model **investigation steps** explicitly, and model the **environment** (version, modules, integrations) as structured data so applicability can be computed.

---

## 3. Knowledge & solution management

### 3.1 KCS: Knowledge-Centered Service (Consortium for Service Innovation)

KCS is the industry methodology for support knowledge. Its core ideas map almost one-to-one to the brief:

- **Solve Loop** (per case): *Capture* knowledge in the workflow, *Structure* it in a standard template, *Reuse* it by searching early and often, *Improve* it every time it is reused.
- **Evolve Loop** (organisation level): content health, process integration, performance assessment, leadership.
- **"Search early, search often."** Searching the knowledge base is the first step of every case, not the last resort.
- **"Reuse is review."** Every time an article is reused, it gets validated or corrected, so knowledge quality improves through use instead of separate editorial work.
- **Standard article structure:** *Issue* (customer's words), *Environment* (product, version, config), *Resolution*, *Cause*.
- **Article lifecycle:** Work in Progress → Not Validated → Validated → Archived.

**→ Implication:**
- Solution schema = **Issue / Environment / Cause / Resolution** (+ verification steps).
- Solution status follows the KCS lifecycle: `draft → not_validated → validated → archived`.
- Every case outcome updates the solution's **track record** ("reuse is review").
- The AI runs a search **automatically on case creation** ("search early").

### 3.2 ITIL 4: Incident, Problem, Known Error

- **Incident:** one occurrence of a disruption (≈ our *Case*).
- **Problem:** the underlying cause of one or more incidents.
- **Known Error:** a problem with a documented root cause and workaround (≈ our *Solution*).

Distinguishing these matters: ten cases about "products missing in webshop" may come from **one** problem (e.g. a changed export mapping in a release). Spotting such clusters early is very valuable for a software vendor, because it points at product bugs.

**→ Implication:** a root-cause taxonomy shared across cases and solutions. Stretch goal: a *Problem Management agent* that clusters recurring cases without a solution and proposes a Problem record.

---

## 4. How historical cases help solve new problems: Case-Based Reasoning

Case-Based Reasoning (CBR, Aamodt & Plaza, 1994) describes how experts reuse past experience as a 4-step cycle:

1. **Retrieve** the most similar past case(s)
2. **Reuse** their solution for the new problem
3. **Revise** the proposed solution (test it, adapt it)
4. **Retain** the new experience for future use

This is exactly the brief's flow: *New Case → Historical Knowledge → AI Assistance → Engineer → Solution → New Knowledge*. CBR also shows that **similarity is more than text similarity**: matching on structured features (here: module, version, integration, error code) is as important as matching on the description.

**→ Implication:** the platform's architecture follows the CBR cycle. Retrieval uses **text + structured features**. Reuse includes an **applicability check**. Revise is the engineer's investigation. Retain is the **knowledge capture** step.

---

## 5. Search & similarity

| Technique | Strength | Weakness in support data |
|---|---|---|
| **Lexical / full-text (BM25, PostgreSQL FTS)** | Exact error codes, class names, log strings, version numbers | Misses paraphrases ("not showing" vs "missing") |
| **Dense vector embeddings** | Semantic paraphrase matching, multilingual | Weak on exact identifiers; "ERR-4012" and "ERR-4021" look alike to an embedding |
| **Trigram / fuzzy** | Typos, partial identifiers | Noisy on long text |
| **Structured filters / boosts** | Module, version, integration, customer environment | Needs structured data to exist |
| **LLM re-ranking** | Judges *relevance to this case*, can explain why | Cost and latency, so only on a short candidate list |

**Hybrid retrieval** combines lexical and dense results. A robust, parameter-light way to merge rankings is **Reciprocal Rank Fusion** (Cormack, Clarke & Buettcher, 2009): `score = Σ 1 / (k + rank_i)`. Support data mixes natural-language symptoms with exact technical tokens, which is the textbook case where hybrid search beats either method alone. The evaluation harness will test that claim on our own data (ablation study).

**→ Implication:** PostgreSQL does it all in one database: `tsvector` FTS + `pgvector` + `pg_trgm`, merged via RRF in a SQL function, boosted by structured environment similarity, then **LLM re-ranking** of the top candidates with a written rationale.

---

## 6. AI-assisted problem solving

- **Retrieval-Augmented Generation (RAG)** (Lewis et al., 2020): ground LLM output in retrieved documents instead of model memory. Essential here, because the model knows nothing about Viamedici's internal cases.
- **Grounding & citations:** every AI claim should point to the case/solution it came from, so the engineer can verify it in one click. Citations must be *validated*: an ID the model cites must exist in the retrieved set.
- **Hallucination risk:** an invented "solution" in an enterprise context costs trust and engineer time. Mitigations: structured outputs, citation validation, explicit "no relevant history found" outcome, confidence levels.
- **Human-in-the-loop:** the brief says *"the engineer remains responsible."* AI output must be **suggestions** that a human accepts, edits or rejects, and those decisions are useful feedback data.

**→ Implication:** agents never write directly to cases or solutions. They write **suggestions** (with rationale, confidence, citations). Humans approve. Accept/reject decisions are stored and used to evaluate and improve.

---

## 7. Agentic AI / agent architectures

- **ReAct** (Yao et al., 2022): interleaving reasoning and tool calls lets a model search, look at results, and decide to search again differently. That is how an engineer actually searches.
- **Tool use / function calling:** the model calls typed functions (search, fetch case, fetch release notes) instead of guessing.
- **Workflows vs agents** (Anthropic, *Building Effective Agents*, 2024): use predictable workflows (prompt chaining, routing, orchestrator-workers, evaluator-optimizer) where the steps are known, and give autonomy only where the path genuinely varies. Multi-agent systems add cost, latency and failure modes, so the agency should *earn its place*.
- **Stateful graphs** (e.g. LangGraph): model the process as a graph of nodes with shared state, checkpoints and **interrupts** for human approval, which is a good fit for an enterprise process with sign-off steps.

**Where autonomy pays off in this problem:**

| Step | Workflow or agent? | Why |
|---|---|---|
| Structure the incoming case | Workflow (single structured LLM call) | Fixed input → fixed schema |
| Find similar history | **Agent** (ReAct loop with tools) | Needs query reformulation, filter changes, following case → solution → case links |
| Judge applicability | Workflow (evaluator) | Compare environment vs solution conditions + release notes |
| Plan the investigation | **Agent** | Depends on what was found and on the engineer's findings so far; re-plans each step |
| Capture knowledge | Workflow + human interrupt | Decide create / merge / link, then wait for approval |

**→ Implication:** a **graph-orchestrated workflow with agentic nodes**, not a free-form swarm of chatting agents. Easy to explain, debug, evaluate and defend.

---

## 8. Domain research: what Viamedici's customers run into

Viamedici's EPIM is an enterprise **PIM / MDM** platform: companies use it to model, enrich, classify and publish product information to many channels. Typical moving parts:

- **Product data modelling:** product types, attributes, variants, inheritance
- **Imports:** from ERP systems (e.g. SAP), supplier data, spreadsheets
- **Classification standards:** ETIM, ECLASS
- **Exchange formats:** BMEcat, XML/CSV/JSON exports
- **Channel syndication:** webshops (e.g. Shopware, Magento/Adobe Commerce, SAP Commerce), marketplaces, print catalogues
- **Media / DAM:** images, documents, renditions
- **Workflows:** enrichment, approval, release
- **Multi-language:** translations, locale fallbacks
- **Integrations and APIs**, scheduled jobs, permissions

**Typical problem classes** (used to design a realistic dataset):

| Class | Example symptom | Typical cause |
|---|---|---|
| Export / channel | Products missing or outdated on webshop | Export filter, mapping change after upgrade, failed scheduled job, channel API change |
| Import | ERP import partially fails | Changed source field, unit mismatch, encoding, duplicate keys |
| Classification | ETIM features missing after class update | Version change of classification standard, unmapped features |
| Data model / inheritance | Variant shows parent's old value | Inheritance override, cache |
| Media | Images not shown in channel | Rendition config, file path, CDN cache |
| Localisation | Wrong language on export | Locale fallback chain configuration |
| Workflow | Products stuck in approval | Role/permission change, workflow condition |
| Performance | Export takes hours after data growth | Missing index, unbatched job, full instead of delta export |
| Upgrade regression | Behaviour changed after update | Breaking change in release, deprecated config |

**→ Implication:** the synthetic dataset models **customers, environments (version + modules + integrations), releases with breaking-change notes**, and cases across these classes. It deliberately includes **version-specific fixes**, **near-duplicates**, **red herrings** and **recurring clusters**, so the AI's judgement can be shown and measured.

---

## 9. Summary: research → design decisions

| # | Finding | Design decision |
|---|---|---|
| D1 | Ticket tools are ticket-centric; knowledge is a by-product | Separate **Case** and **Solution** entities; the solution is the reusable asset |
| D2 | Outcome evidence is missing in most tools | `case_solutions` relation with **outcome**; solutions carry a **track record** |
| D3 | Similarity ignores the environment | Structured **environment** (version, modules, integrations) + **applicability check** |
| D4 | The investigation path is lost | **Investigation steps** with confirmed / ruled-out results |
| D5 | KCS: search early, reuse is review, standard structure | Auto-analysis on case creation; Issue/Environment/Cause/Resolution template; KCS lifecycle |
| D6 | CBR: Retrieve → Reuse → Revise → Retain | The platform's end-to-end flow follows CBR |
| D7 | Support text mixes language and exact identifiers | **Hybrid search** (FTS + vectors + trigram + structured boosts, RRF) + LLM re-rank |
| D8 | Hallucinations destroy trust | Grounded, **cited**, validated, structured outputs; "no match" is an allowed answer |
| D9 | Engineer stays responsible | Agents write **suggestions** only; humans approve; feedback is recorded |
| D10 | Agency must earn its place | **Graph workflow with agentic nodes** only where the path varies |
| D11 | Recurring incidents point at product problems | Root-cause taxonomy; stretch: **Problem Management agent** |

## References

- Consortium for Service Innovation: *KCS v6 Practices Guide*
- AXELOS: *ITIL 4: Incident, Problem and Knowledge Management practices*
- Aamodt, A. & Plaza, E. (1994). *Case-Based Reasoning: Foundational Issues, Methodological Variations, and System Approaches.* AI Communications.
- Cormack, G., Clarke, C. & Buettcher, S. (2009). *Reciprocal Rank Fusion outperforms Condorcet and individual rank learning methods.* SIGIR.
- Lewis, P. et al. (2020). *Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks.* NeurIPS.
- Yao, S. et al. (2022). *ReAct: Synergizing Reasoning and Acting in Language Models.*
- Anthropic (2024). *Building Effective Agents.*
- Product documentation: ServiceNow, Jira Service Management, Zendesk, Salesforce Service Cloud; Directus; pgvector; LangGraph.
