// SEP endpoint (/sep/*): the only door between the Directus app and the agent service.
//  - proxies agent runs, search and "ask" (the agent-service secret never reaches the browser)
//  - applies accepted AI suggestions with the *user's* accountability, so Directus permissions decide
//    who may do what (e.g. only Solution Architects can publish knowledge).

const KNOWLEDGE_TYPES = ['knowledge_create', 'knowledge_update', 'knowledge_link'];

export default {
	id: 'sep',
	handler: (router, { services, getSchema, env, logger }) => {
		const { ItemsService } = services;
		const agentUrl = (env.SEP_AGENT_SERVICE_URL || process.env.SEP_AGENT_SERVICE_URL || 'http://agent-service:8000').replace(/\/$/, '');
		const agentSecret = env.SEP_AGENT_SERVICE_SECRET || process.env.SEP_AGENT_SERVICE_SECRET || '';

		async function agent(method, path, body) {
			const res = await fetch(agentUrl + path, {
				method,
				headers: { 'Content-Type': 'application/json', 'X-SEP-Secret': agentSecret },
				body: body === undefined ? undefined : JSON.stringify(body),
			});
			const text = await res.text();
			let data;
			try {
				data = text ? JSON.parse(text) : {};
			} catch {
				data = { detail: text };
			}
			if (!res.ok) {
				const err = new Error(data.detail || `agent service HTTP ${res.status}`);
				err.status = 502;
				throw err;
			}
			return data;
		}

		const wrap = (fn) => async (req, res) => {
			if (!req.accountability?.user) return res.status(401).json({ error: 'Login required' });
			try {
				res.json(await fn(req));
			} catch (e) {
				const status = e.status || (e.code === 'FORBIDDEN' ? 403 : 400);
				logger.warn(`[sep] ${req.method} ${req.path}: ${e.message}`);
				res.status(status).json({ error: e.message });
			}
		};

		async function svc(collection, req) {
			return new ItemsService(collection, { schema: await getSchema(), accountability: req.accountability });
		}

		// ---- agent runs -----------------------------------------------------------------------
		router.post('/cases/:id/analyse', wrap((req) =>
			agent('POST', '/runs/case-analysis', { case_id: req.params.id, trigger: 'manual' })));
		router.post('/cases/:id/investigate', wrap((req) =>
			agent('POST', '/runs/investigation', { case_id: req.params.id, trigger: 'manual' })));
		router.post('/cases/:id/capture', wrap((req) =>
			agent('POST', '/runs/knowledge-capture', { case_id: req.params.id, trigger: 'manual' })));
		router.post('/cases/:id/ask', wrap((req) =>
			agent('POST', '/ask', { case_id: req.params.id, question: String(req.body?.question || '').slice(0, 2000) })));
		router.post('/problems/detect', wrap(() => agent('POST', '/runs/problem-detection')));
		router.get('/search', wrap((req) => {
			const q = new URLSearchParams({ q: String(req.query.q || ''), scope: String(req.query.scope || 'all'), limit: '15' });
			return agent('GET', `/search?${q}`);
		}));

		// ---- resolve a case (engineer) ----------------------------------------------------------
		// Records which solutions were tried with which outcome; status 'resolved' triggers the
		// Knowledge Capture flow.
		router.post('/cases/:id/resolve', wrap(async (req) => {
			const { resolution_summary, root_cause_id, outcomes = [] } = req.body || {};
			if (!resolution_summary) throw new Error('resolution_summary is required');
			const cases = await svc('cases', req);
			const c = await cases.readOne(req.params.id, { fields: ['id', 'epim_version'] });
			for (const o of outcomes) await upsertCaseSolution(req, c, o.solution_id, 'applied', o.outcome, o.notes);
			await cases.updateOne(c.id, {
				resolution_summary, root_cause_id: root_cause_id || null,
				status: 'resolved', resolved_at: new Date().toISOString(),
			});
			return { ok: true };
		}));

		async function upsertCaseSolution(req, c, solutionId, relation, outcome, notes) {
			const cs = await svc('case_solutions', req);
			const found = await cs.readByQuery({
				filter: { case_id: { _eq: c.id }, solution_id: { _eq: solutionId } }, fields: ['id'], limit: 1,
			});
			const data = { relation, outcome, notes: notes || null, version_at_apply: c.epim_version,
				applied_by: req.accountability.user };
			if (found.length) return cs.updateOne(found[0].id, data);
			return cs.createOne({ case_id: c.id, solution_id: solutionId, ...data });
		}

		async function idByCode(req, collection, code) {
			if (!code) return null;
			const rows = await (await svc(collection, req)).readByQuery({ filter: { code: { _eq: code } }, fields: ['id'], limit: 1 });
			return rows[0]?.id || null;
		}

		async function solutionByNumber(req, number) {
			const rows = await (await svc('solutions', req)).readByQuery({
				filter: { solution_number: { _eq: number } }, fields: ['id', 'revision'], limit: 1,
			});
			if (!rows.length) throw new Error(`Unknown solution ${number}`);
			return rows[0];
		}

		async function nextSeq(req, caseId) {
			const rows = await (await svc('investigation_steps', req)).readByQuery({
				filter: { case_id: { _eq: caseId } }, fields: ['seq'], sort: ['-seq'], limit: 1,
			});
			return (rows[0]?.seq || 0) + 1;
		}

		async function closeWithKnowledge(req, c, solutionId) {
			await upsertCaseSolution(req, c, solutionId, 'applied', 'resolved');
			await (await svc('cases', req)).updateOne(c.id, { status: 'closed' });
			agent('POST', `/index/solution/${solutionId}`).catch((e) => logger.warn(`[sep] reindex: ${e.message}`));
		}

		function draftToSolution(draft) {
			return {
				title: draft.title, issue: draft.issue, environment_conditions: draft.environment_conditions,
				cause: draft.cause, resolution_steps: draft.resolution_steps, verification_steps: draft.verification_steps,
				min_version: draft.min_version || null, max_version: draft.max_version || null,
			};
		}

		// ---- apply a suggestion ---------------------------------------------------------------
		const APPLY = {
			async case_structure(req, s, c, edits) {
				const p = { ...s.payload, ...edits };
				const patch = {
					module_id: await idByCode(req, 'modules', p.module_code),
					symptoms: (p.symptoms || []).join('\n') || null,
					error_messages: (p.error_codes || []).join('\n') || null,
					customer_goal: p.customer_goal || null,
				};
				if (p.severity_suggestion) patch.severity = p.severity_suggestion;
				for (const k of Object.keys(patch)) if (patch[k] === null) delete patch[k];
				await (await svc('cases', req)).updateOne(c.id, patch);
			},
			async clarification(req, s, c) {
				await (await svc('cases', req)).updateOne(c.id, { status: 'waiting_customer', missing_info: s.payload.missing_info });
			},
			async next_step(req, s, c, edits) {
				await (await svc('investigation_steps', req)).createOne({
					case_id: c.id, seq: await nextSeq(req, c.id), action: edits.action || s.payload.action || s.title,
					result: 'inconclusive', source: 'agent_suggested', author: req.accountability.user,
				});
				if (['new', 'triage', 'analysing'].includes(c.status)) await (await svc('cases', req)).updateOne(c.id, { status: 'investigating' });
			},
			async hypothesis(req, s, c) {
				await (await svc('investigation_steps', req)).createOne({
					case_id: c.id, seq: await nextSeq(req, c.id),
					action: `Verify hypothesis "${s.title}"${s.payload?.confirm_by ? ': ' + s.payload.confirm_by : ''}`,
					result: 'inconclusive', source: 'agent_suggested', author: req.accountability.user,
				});
			},
			async solution(req, s, c) {
				const sol = await solutionByNumber(req, s.payload.number);
				await upsertCaseSolution(req, c, sol.id, 'referenced', 'not_tried', s.payload.adaptations);
			},
			async similar_case(req, s, c) {
				if (!s.payload.entity_id || s.payload.entity_id === c.id) return;
				const links = await svc('case_links', req);
				const found = await links.readByQuery({
					filter: { from_case_id: { _eq: c.id }, to_case_id: { _eq: s.payload.entity_id } }, fields: ['id'], limit: 1 });
				if (!found.length) await links.createOne({ from_case_id: c.id, to_case_id: s.payload.entity_id, link_type: 'similar', created_by: 'agent' });
			},
			async knowledge_link(req, s, c) {
				await closeWithKnowledge(req, c, s.payload.target_solution_id || (await solutionByNumber(req, s.payload.target_solution)).id);
			},
			async knowledge_update(req, s, c, edits) {
				const target = s.payload.target_solution_id
					? await (await svc('solutions', req)).readOne(s.payload.target_solution_id, { fields: ['id', 'revision'] })
					: await solutionByNumber(req, s.payload.target_solution);
				const draft = { ...(s.payload.solution || {}), ...edits };
				const patch = s.payload.solution ? draftToSolution(draft) : {};
				await (await svc('solutions', req)).updateOne(target.id, {
					...patch, revision: (target.revision || 1) + 1, status: 'validated',
					approved_by: req.accountability.user, approved_at: new Date().toISOString(),
				});
				await closeWithKnowledge(req, c, target.id);
			},
			async knowledge_create(req, s, c, edits) {
				const draft = { ...(s.payload.solution || {}), ...edits };
				if (!draft.title || !draft.resolution_steps) throw new Error('The draft needs a title and resolution steps');
				const solutions = await svc('solutions', req);
				const id = await solutions.createOne({
					...draftToSolution(draft), root_cause_id: await idByCode(req, 'root_causes', draft.root_cause_code),
					status: 'validated', ai_generated: true, approved_by: req.accountability.user,
					approved_at: new Date().toISOString(),
				});
				const sm = await svc('solution_modules', req);
				for (const code of draft.module_codes || []) {
					const mid = await idByCode(req, 'modules', code);
					if (mid) await sm.createOne({ solution_id: id, module_id: mid });
				}
				await closeWithKnowledge(req, c, id);
				return { solution_id: id };
			},
			async problem(req, s) {
				const id = await (await svc('problems', req)).createOne({
					title: s.payload.title || s.title, description: [s.payload.description,
						s.payload.suspected_cause && `Suspected cause: ${s.payload.suspected_cause}`,
						s.payload.workaround && `Workaround: ${s.payload.workaround}`]
						.filter(Boolean).join('\n\n'), status: 'open', ai_generated: true,
				});
				const caseIds = s.payload.case_ids || [];
				if (caseIds.length) await (await svc('cases', req)).updateMany(caseIds, { problem_id: id });
				return { problem_id: id };
			},
		};

		router.post('/suggestions/:id/decide', wrap(async (req) => {
			const { decision, note, edits = {} } = req.body || {};
			if (!['accept', 'reject'].includes(decision)) throw new Error("decision must be 'accept' or 'reject'");
			const suggestions = await svc('ai_suggestions', req);
			const s = await suggestions.readOne(req.params.id, { fields: ['*'] });
			if (s.status !== 'pending') throw new Error(`Suggestion is already ${s.status}`);
			let result = {};
			if (decision === 'accept') {
				const c = s.case_id ? await (await svc('cases', req)).readOne(s.case_id, { fields: ['id', 'status', 'epim_version'] }) : null;
				const apply = APPLY[s.type];
				if (apply) result = (await apply(req, s, c, edits)) || {};
			}
			await suggestions.updateOne(s.id, {
				status: decision === 'accept' ? 'accepted' : 'rejected', feedback_note: note || null,
				decided_by: req.accountability.user, decided_at: new Date().toISOString(),
			});
			if (decision === 'accept' && KNOWLEDGE_TYPES.includes(s.type) && s.case_id) {
				// one knowledge decision per case: close the alternatives
				const others = await suggestions.readByQuery({ filter: { case_id: { _eq: s.case_id }, status: { _eq: 'pending' },
					type: { _in: KNOWLEDGE_TYPES } }, fields: ['id'] });
				if (others.length) await suggestions.updateMany(others.map((o) => o.id), { status: 'superseded' });
			}
			return { ok: true, ...result };
		}));
	},
};
