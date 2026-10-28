# 01 Research

Before designing anything I looked at how existing support tools handle knowledge, which methods already exist for reusing past cases, and what kind of problems Viamedici's customers actually have. Each section ends with what I took from it for the design. The decisions are numbered D1 to D11 at the end, and the product document refers to them.

## 1. The problem

When a solution engineer solves a customer case, what they learned ends up in three places:

- the ticket history, written for that moment and hard to reuse later
- the engineer's own head, which is lost when people change teams or leave
- documents and wiki pages, which are structured but not connected to the cases they came from

So a new case often gets investigated from scratch, even though someone solved something similar a year ago. Storing the history isn't the hard part. The hard part is finding the right old case at the right time and knowing if its fix still applies.

An old solution only helps if:

1. it can be found even when the new case is written in different words
2. the engineer can see whether it actually worked before, and where it didn't
3. the engineer can tell if it applies to this customer (same module, version, integrations)
4. new solutions get back into the knowledge base without much extra work

**Takeaway:** this shouldn't be another ticket system. It should be a loop around case work: find old knowledge, use it, and save what was learned.

## 2. Existing case and ticket tools

| Tool | Good at | AI / knowledge features | What's missing for this problem |
|---|---|---|---|
| ServiceNow ITSM / CSM | ITIL processes (incident, problem, known error), workflows | Similar incident suggestions, article recommendations, case summaries | Knowledge articles are separate from cases; whether a fix worked in a given environment isn't really tracked |
| Jira Service Management | Linking tickets to dev issues, Confluence as knowledge base | Suggested articles, ticket summaries, grouping related issues | Knowledge is free text in Confluence, no outcomes per solution |
| Zendesk | Fast agent UI, macros, help center | Bots for deflection, intent detection, suggested macros | Built for high-volume B2C support, not long technical investigations |
| Salesforce Service Cloud | Cases linked to CRM and customer data | Case classification, article recommendations, reply drafts | Strong on customer data, weak on technical environment and investigation steps |

Things worth copying from them: linking articles to cases, showing suggestions while the case is being worked on (not in a separate search page), status workflows, roles and an audit trail.

Things they mostly don't do:

1. The ticket is the main thing and knowledge comes second.
2. They don't record outcomes, e.g. "applied 8 times, worked 7 times, failed once on version X".
3. Similarity is based on text only. Version, modules and integrations are ignored.
4. Only the final answer is kept, not the investigation. What was checked and ruled out is often the most useful part for hard cases.

**Takeaway:** cases and solutions should be separate tables, joined by a relation that records the outcome. Investigation steps get their own table, and the customer environment (version, modules, integrations) is structured data so applicability can be checked.

## 3. Knowledge management methods

### KCS (Knowledge-Centered Service)

KCS is the method most support organisations use for knowledge. A lot of it matches the brief:

- Knowledge is captured while solving the case, in a fixed structure, and improved every time it's reused.
- "Search early, search often": search the knowledge base at the start of every case.
- "Reuse is review": each reuse checks whether the article is still correct.
- Article structure: Issue, Environment, Resolution, Cause.
- Lifecycle: work in progress, not validated, validated, archived.

**Takeaway:**

- Solutions use the fields Issue, Environment, Cause, Resolution plus verification steps.
- Status follows KCS: `draft`, `not_validated`, `validated`, `archived`.
- Every case outcome updates the solution's track record.
- The analysis starts automatically when a case is created.

### ITIL 4

ITIL separates an **incident** (one occurrence, like our case), a **problem** (the underlying cause behind one or more incidents) and a **known error** (a problem with a known cause and workaround, like our solution).

This matters for a software vendor. Ten cases about products missing in the webshop could all come from one change in a release, which is really a product bug.

**Takeaway:** a shared root cause list for cases and solutions, and a problem detection agent that groups open cases with the same symptoms and proposes a problem record.

## 4. Case-based reasoning

Case-based reasoning (Aamodt & Plaza, 1994) describes how experts use past experience in four steps:

1. Retrieve the most similar past cases
2. Reuse their solution
3. Revise it for the new situation
4. Retain the new experience

This is basically the flow in the brief (new case, history, AI help, engineer, solution, new knowledge). The paper also points out that similarity isn't only about text. Structured features matter just as much, which here means module, version, integration and error codes.

**Takeaway:** the platform follows these four steps. Search uses text and structured features, reuse includes an applicability check, revise is the engineer's investigation, and retain is knowledge capture.

## 5. Search

| Method | Good at | Bad at |
|---|---|---|
| Full text (PostgreSQL FTS, BM25) | Exact error codes, class names, version numbers | Different wording ("not showing" vs "missing") |
| Vector embeddings | Same meaning in different words | Exact codes, "ERR-4012" and "ERR-4021" look almost the same |
| Trigram | Typos and partial codes | Noisy on long text |
| Filters / boosts | Module, version, integration | Needs structured data |
| LLM judging | Relevance to this specific case, with a reason | Slow and costs tokens, so only for a few candidates |

Support tickets mix normal sentences with exact technical strings, so neither full text nor vectors are enough on their own. Reciprocal Rank Fusion (Cormack et al., 2009) is a simple way to combine several rankings: each result gets `1 / (k + rank)` from each list and the scores are added up.

**Takeaway:** everything stays in PostgreSQL: FTS, pgvector and pg_trgm, combined with RRF in one SQL function and boosted by environment match. An LLM then judges the top few results and explains why.

## 6. Using LLMs safely

- **RAG** (Lewis et al., 2020): the model answers from retrieved documents, not from its own memory. Needed here because the model knows nothing about Viamedici's cases.
- **Citations:** every claim should point to the case or solution it came from, and cited IDs are checked against what was actually retrieved.
- **Hallucinations:** a made-up solution wastes the engineer's time and trust. Ways to reduce this: structured output, checked citations, allowing "nothing relevant found" as an answer, and confidence levels.
- **Human in the loop:** the brief says the engineer stays responsible, so AI output should be suggestions the engineer accepts, edits or rejects.

**Takeaway:** agents never change cases or solutions directly. They write suggestions with a reason, confidence and sources. A person approves them, and accept/reject decisions are saved as feedback.

## 7. Agent design

- **ReAct** (Yao et al., 2022): the model alternates between thinking and calling tools, so it can search, look at the results and search again differently, much like an engineer does.
- **Tool calling:** the model calls defined functions (search, get case, get release notes) instead of guessing.
- **Workflows vs agents** (Anthropic, *Building Effective Agents*, 2024): use fixed workflows where the steps are known and only let the model decide where the path really varies. More agents means more cost, latency and things that can go wrong.
- **LangGraph:** the process is a graph of steps with shared state, which fits a business process with approval steps.

| Step | Fixed step or agent | Why |
|---|---|---|
| Structure the new case | Fixed (one structured LLM call) | Same input and output every time |
| Find similar history | Agent with tools | May need to rephrase the query, change filters, follow links between cases and solutions |
| Check applicability | Fixed | Compare environment with solution conditions and release notes |
| Plan next steps | Fixed, re-run after each finding | Depends on what was found and what the engineer has logged |
| Capture knowledge | Fixed, then wait for approval | Decide create, update or link, then a person approves |

**Takeaway:** one graph workflow with a single tool-using agent where it's needed, not a group of agents talking to each other. It's easier to explain, debug and test.

## 8. The domain: Viamedici EPIM

EPIM is a PIM/MDM platform. Companies use it to model, enrich, classify and publish product data to different channels. The main parts:

- product data model: product types, attributes, variants, inheritance
- imports from ERP (e.g. SAP), suppliers, spreadsheets
- classification standards (ETIM, ECLASS)
- exchange formats (BMEcat, XML, CSV, JSON)
- channels: webshops (Shopware, Adobe Commerce, SAP Commerce), marketplaces, print
- media and DAM
- workflows for enrichment, approval and release
- multiple languages
- integrations, APIs, scheduled jobs, permissions

Common problem types, which I used to build the sample dataset:

| Type | Example | Usual cause |
|---|---|---|
| Export / channel | Products missing or outdated in webshop | Export filter, mapping changed by upgrade, failed job, channel API change |
| Import | ERP import partly fails | Changed source field, units, encoding, duplicate keys |
| Classification | ETIM features missing after update | New standard version, unmapped features |
| Data model | Variant shows old parent value | Inheritance override, cache |
| Media | Images missing in channel | Rendition config, file path, CDN cache |
| Language | Wrong language in export | Locale fallback settings |
| Workflow | Products stuck in approval | Role or permission change, workflow condition |
| Performance | Export takes hours | Missing index, no batching, full export instead of delta |
| Upgrade | Behaviour changed after update | Breaking change or deprecated setting |

**Takeaway:** the dataset has customers, environments (version, modules, integrations), releases with breaking changes, and cases across these types. It includes fixes that only work on certain versions, near duplicates, misleading matches and groups of recurring cases, so the agents' judgement can actually be tested.

## 9. Design decisions

| # | Finding | Decision |
|---|---|---|
| D1 | Ticket tools treat knowledge as a side product | Separate Case and Solution tables; the solution is what gets reused |
| D2 | Most tools don't track outcomes | `case_solutions` with an outcome; each solution has a track record |
| D3 | Similarity ignores the environment | Structured environment data and an applicability check |
| D4 | The investigation gets lost | Investigation steps with confirmed / ruled out results |
| D5 | KCS: search early, reuse is review, fixed structure | Analysis on case creation, Issue/Environment/Cause/Resolution, KCS statuses |
| D6 | Case-based reasoning: retrieve, reuse, revise, retain | The main flow follows these steps |
| D7 | Support text mixes sentences and exact codes | Hybrid search (FTS, vectors, trigram, boosts, RRF), then LLM judging |
| D8 | Made-up answers break trust | Sources on every suggestion, checked citations, "no match" allowed |
| D9 | The engineer is responsible | Agents only write suggestions; people approve; feedback saved |
| D10 | Only use an agent where the path varies | Graph workflow with one tool-using agent for retrieval |
| D11 | Repeated incidents point to product problems | Root cause list and a problem detection agent |

## References

- Consortium for Service Innovation, *KCS v6 Practices Guide*
- AXELOS, *ITIL 4: Incident, Problem and Knowledge Management practices*
- Aamodt, A. & Plaza, E. (1994). *Case-Based Reasoning: Foundational Issues, Methodological Variations, and System Approaches.* AI Communications.
- Cormack, G., Clarke, C. & Buettcher, S. (2009). *Reciprocal Rank Fusion outperforms Condorcet and individual rank learning methods.* SIGIR.
- Lewis, P. et al. (2020). *Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks.* NeurIPS.
- Yao, S. et al. (2022). *ReAct: Synergizing Reasoning and Acting in Language Models.*
- Anthropic (2024). *Building Effective Agents.*
- Product documentation: ServiceNow, Jira Service Management, Zendesk, Salesforce Service Cloud, Directus, pgvector, LangGraph.
