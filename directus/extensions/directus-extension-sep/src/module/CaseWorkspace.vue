<template>
	<private-view :title="c ? `${c.case_number} · ${c.title}` : 'Case'">
		<template #headline><v-breadcrumb :items="[{ name: 'Cases', to: '/sep-workspace' }]" /></template>
		<template #title-outer:prepend>
			<v-button class="header-icon" rounded icon secondary to="/sep-workspace"><v-icon name="arrow_back" /></v-button>
		</template>
		<template #navigation><module-nav /></template>
		<template #actions>
			<v-button rounded icon secondary v-tooltip.bottom="'Open in data studio'" :to="`/content/cases/${id}`"><v-icon name="edit" /></v-button>
		</template>

		<div v-if="!c" class="loading"><v-progress-circular indeterminate /></div>
		<div v-else class="workspace">
			<!-- ============ left: the case ============ -->
			<div class="col">
				<section class="panel">
					<div class="meta">
						<span class="status" :style="{ '--c': STATUS_COLOR[c.status] }">{{ STATUS_LABEL[c.status] }}</span>
						<span><v-icon name="business" x-small /> {{ c.environment_id?.customer_id?.name }}</span>
						<span><v-icon name="deployed_code" x-small /> EPIM {{ c.epim_version }}</span>
						<span v-if="c.module_id"><v-icon name="widgets" x-small /> {{ c.module_id.code }}</span>
						<span :class="['sev', c.severity]">{{ c.severity }}</span>
						<span v-if="c.assigned_to"><v-icon name="person" x-small /> {{ c.assigned_to.first_name }} {{ c.assigned_to.last_name }}</span>
					</div>
					<div v-if="c.problem_id" class="problem-link"><v-icon name="bug_report" small /> Linked problem: {{ c.problem_id.title }}</div>
					<h3>Customer report</h3>
					<p class="report">{{ c.customer_report }}</p>
					<div v-if="c.symptoms || c.error_messages || c.customer_goal" class="structured">
						<div v-if="c.customer_goal"><b>Goal:</b> {{ c.customer_goal }}</div>
						<div v-if="c.symptoms"><b>Symptoms:</b> {{ c.symptoms }}</div>
						<div v-if="c.error_messages"><b>Errors:</b> <code>{{ c.error_messages }}</code></div>
					</div>
					<div v-if="c.environment_id?.config_notes" class="env-notes"><b>Environment notes:</b> {{ c.environment_id.config_notes }}</div>
				</section>

				<section class="panel">
					<h2><v-icon name="checklist" /> Investigation log</h2>
					<ol class="steps">
						<li v-for="st in steps" :key="st.id" :class="st.result">
							<div class="step-head">
								<span class="result">{{ RESULT[st.result] }}</span>
								<span class="action">{{ st.action }}</span>
								<v-icon v-if="st.source === 'agent_suggested'" name="smart_toy" x-small v-tooltip="'Suggested by AI, accepted by engineer'" />
							</div>
							<div v-if="st.finding" class="finding">{{ st.finding }}</div>
							<div v-else-if="editing !== st.id" class="add-finding">
								<v-button x-small secondary @click="startFinding(st)">Record finding</v-button>
							</div>
							<div v-if="editing === st.id" class="finding-form">
								<v-textarea v-model="findingForm.finding" placeholder="What did you find?" />
								<div class="row">
									<v-select v-model="findingForm.result" :items="resultItems" />
									<v-button small :loading="saving" @click="saveFinding(st)">Save</v-button>
									<v-button small secondary @click="editing = null">Cancel</v-button>
								</div>
							</div>
						</li>
						<li v-if="!steps.length" class="muted empty">No steps yet. Accept a suggested next step or log your own.</li>
					</ol>
					<div class="new-step">
						<v-input v-model="stepForm.action" placeholder="What did you check?" />
						<v-textarea v-model="stepForm.finding" placeholder="What did you find? (optional)" />
						<div class="row">
							<v-select v-model="stepForm.result" :items="resultItems" />
							<v-button :disabled="!stepForm.action" :loading="saving" @click="addStep">Log step</v-button>
						</div>
					</div>
				</section>

				<section v-if="appliedSolutions.length" class="panel">
					<h2><v-icon name="menu_book" /> Solutions on this case</h2>
					<div v-for="cs in appliedSolutions" :key="cs.id" class="applied">
						<router-link :to="`/content/solutions/${cs.solution_id.id}`" class="mono">{{ cs.solution_id.solution_number }}</router-link>
						<span class="grow">{{ cs.solution_id.title }}</span>
						<span class="muted">{{ cs.relation }} · {{ cs.outcome }}</span>
					</div>
				</section>

				<section v-if="canResolve" class="panel">
					<h2><v-icon name="task_alt" /> Resolve case</h2>
					<p class="muted">Resolving starts the Knowledge Capture agent, which proposes to link, update or create a solution.</p>
					<label>Resolution summary</label>
					<v-textarea v-model="resolveForm.summary" placeholder="What fixed it?" />
					<label>Root cause</label>
					<v-select v-model="resolveForm.root_cause_id" :items="rootCauseItems" placeholder="Select root cause" />
					<template v-if="appliedSolutions.length">
						<label>Outcome per solution</label>
						<div v-for="cs in appliedSolutions" :key="cs.id" class="row">
							<span class="mono grow">{{ cs.solution_id.solution_number }}</span>
							<v-select v-model="resolveForm.outcomes[cs.solution_id.id]" :items="outcomeItems" />
						</div>
					</template>
					<v-button :disabled="!resolveForm.summary" :loading="saving" @click="resolve">Resolve case</v-button>
				</section>
				<v-notice v-if="error" type="danger">{{ error }}</v-notice>
			</div>

			<!-- ============ right: AI copilot ============ -->
			<div class="col ai">
				<section class="panel copilot">
					<div class="copilot-head">
						<h2><v-icon name="smart_toy" /> AI copilot</h2>
						<div class="buttons">
							<v-button small secondary :loading="starting === 'analyse'" :disabled="running" @click="start('analyse')">
								<v-icon name="refresh" left small /> Re-analyse
							</v-button>
							<v-button small :loading="starting === 'investigate'" :disabled="running" @click="start('investigate')">
								<v-icon name="explore" left small /> Suggest next steps
							</v-button>
						</div>
					</div>

					<div v-if="run" class="run">
						<div class="run-head" @click="traceOpen = !traceOpen">
							<v-progress-circular v-if="run.status === 'running'" indeterminate x-small />
							<v-icon v-else :name="run.status === 'succeeded' ? 'check_circle' : 'error'" small :class="run.status" />
							<span><b>{{ GRAPH[run.graph] || run.graph }}</b> · {{ run.status }}</span>
							<span class="muted">{{ run.latency_ms ? (run.latency_ms / 1000).toFixed(1) + ' s' : '' }}
								<template v-if="run.llm_calls"> · {{ run.llm_calls }} LLM calls · {{ run.model }}</template></span>
							<v-icon :name="traceOpen ? 'expand_less' : 'expand_more'" small class="grow-right" />
						</div>
						<div v-if="run.error" class="run-error">{{ run.error }}</div>
						<ol v-if="traceOpen" class="trace">
							<li v-for="t in trace" :key="t.id" :class="t.type">
								<span class="node">{{ t.node }}</span>
								<v-icon :name="STEP_ICON[t.type] || 'circle'" x-small />
								<span class="summary">{{ t.summary }}</span>
							</li>
						</ol>
					</div>

					<v-notice v-if="!suggestions.length && !running" type="info">No open suggestions. Run an analysis to get help.</v-notice>

					<template v-for="g in groups" :key="g.type">
						<div v-if="byType[g.type]?.length" class="group">
							<h3><v-icon :name="g.icon" small /> {{ g.label }}</h3>
							<p v-if="g.hint" class="muted hint">{{ g.hint }}</p>
							<suggestion-card v-for="s in byType[g.type]" :key="s.id" :s="s" @decided="reload" />
						</div>
					</template>
				</section>

				<section class="panel">
					<h2><v-icon name="forum" /> Ask about this case</h2>
					<div class="row">
						<v-input v-model="question" placeholder="e.g. Did SOL-001 ever work for this customer after 9.0?" @keyup.enter="ask" />
						<v-button :loading="asking" :disabled="!question" @click="ask">Ask</v-button>
					</div>
					<div v-for="(a, i) in answers" :key="i" class="answer">
						<div class="q">{{ a.q }}</div>
						<div class="a">{{ a.answer }}</div>
						<div v-if="a.citations?.length" class="muted cites">Sources: {{ a.citations.join(', ') }}</div>
					</div>
				</section>
			</div>
		</div>
	</private-view>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue';
import { useApi } from '@directus/extensions-sdk';
import ModuleNav from './ModuleNav.vue';
import SuggestionCard from './SuggestionCard.vue';
import { STATUS_COLOR, STATUS_LABEL, errorText } from './util.js';

const props = defineProps({ id: { type: String, required: true } });
const api = useApi();

const RESULT = { confirmed: '✓ Confirmed', ruled_out: '✗ Ruled out', inconclusive: '? Open' };
const GRAPH = { case_analysis: 'Case analysis', investigation_guide: 'Investigation guide', knowledge_capture: 'Knowledge capture', ask_case: 'Ask', problem_detection: 'Problem detection' };
const STEP_ICON = { node_start: 'play_arrow', tool_call: 'build', tool_result: 'data_object', output: 'output', guardrail: 'shield', error: 'error' };
const groups = [
	{ type: 'knowledge_create', label: 'Knowledge review: new solution', icon: 'library_add', hint: 'Review and edit the draft, then publish it to the knowledge base.' },
	{ type: 'knowledge_update', label: 'Knowledge review: update solution', icon: 'edit_note' },
	{ type: 'knowledge_link', label: 'Knowledge review: link existing solution', icon: 'link' },
	{ type: 'solution', label: 'Matching solutions', icon: 'menu_book', hint: 'Ranked by applicability to this customer, version and history.' },
	{ type: 'hypothesis', label: 'Hypotheses', icon: 'psychology' },
	{ type: 'next_step', label: 'Next steps', icon: 'directions_run' },
	{ type: 'clarification', label: 'Missing information', icon: 'help' },
	{ type: 'similar_case', label: 'Similar cases', icon: 'content_copy' },
	{ type: 'case_structure', label: 'Case structure', icon: 'account_tree' },
];
const resultItems = [{ text: 'Confirmed', value: 'confirmed' }, { text: 'Ruled out', value: 'ruled_out' }, { text: 'Inconclusive', value: 'inconclusive' }];
const outcomeItems = [{ text: 'Resolved', value: 'resolved' }, { text: 'Partial', value: 'partial' }, { text: 'Failed', value: 'failed' }, { text: 'Not tried', value: 'not_tried' }];

const c = ref(null);
const steps = ref([]);
const suggestions = ref([]);
const appliedSolutions = ref([]);
const rootCauses = ref([]);
const run = ref(null);
const trace = ref([]);
const traceOpen = ref(true);
const starting = ref('');
const saving = ref(false);
const error = ref('');
const question = ref('');
const asking = ref(false);
const answers = ref([]);
const editing = ref(null);
const findingForm = reactive({ finding: '', result: 'confirmed' });
const stepForm = reactive({ action: '', finding: '', result: 'inconclusive' });
const resolveForm = reactive({ summary: '', root_cause_id: null, outcomes: {} });
let timer = null;
let watchUntil = 0;

const running = computed(() => run.value?.status === 'running' || ['analysing'].includes(c.value?.status));
const byType = computed(() => {
	const out = {};
	for (const s of suggestions.value) (out[s.type] ||= []).push(s);
	for (const k of Object.keys(out)) out[k].sort((a, b) => (a.rank || 0) - (b.rank || 0));
	return out;
});
const canResolve = computed(() => c.value && ['triage', 'investigating', 'waiting_customer', 'new'].includes(c.value.status));
const rootCauseItems = computed(() => rootCauses.value.map((r) => ({ text: `${r.name} (${r.category})`, value: r.id })));

async function loadCase() {
	const r = await api.get(`/items/cases/${props.id}`, { params: { fields: [
		'*', 'environment_id.customer_id.name', 'environment_id.config_notes', 'module_id.code', 'module_id.name',
		'assigned_to.first_name', 'assigned_to.last_name', 'problem_id.title'].join(',') } });
	c.value = r.data.data;
}

async function loadSide() {
	const [st, sg, cs] = await Promise.all([
		api.get('/items/investigation_steps', { params: { filter: { case_id: { _eq: props.id } }, sort: 'seq,created_at', limit: -1 } }),
		api.get('/items/ai_suggestions', { params: { filter: { case_id: { _eq: props.id }, status: { _in: ['pending', 'accepted'] } }, limit: -1 } }),
		api.get('/items/case_solutions', { params: { filter: { case_id: { _eq: props.id } }, fields: '*,solution_id.id,solution_id.solution_number,solution_id.title', limit: -1 } }),
	]);
	steps.value = st.data.data;
	suggestions.value = sg.data.data;
	appliedSolutions.value = cs.data.data;
	for (const x of appliedSolutions.value) resolveForm.outcomes[x.solution_id.id] ||= x.outcome === 'not_tried' ? 'resolved' : x.outcome;
}

async function loadRun() {
	const r = await api.get('/items/agent_runs', { params: { filter: { case_id: { _eq: props.id }, graph: { _neq: 'ask_case' } }, sort: '-started_at', limit: 1 } });
	const latest = r.data.data[0];
	const finished = run.value?.status === 'running' && latest?.status !== 'running' && latest?.id === run.value?.id;
	run.value = latest || null;
	if (latest) {
		trace.value = (await api.get('/items/agent_steps', { params: { filter: { run_id: { _eq: latest.id } }, sort: 'seq', limit: -1, fields: 'id,node,type,summary' } })).data.data;
	}
	if (finished) await reload();
}

async function reload() {
	try {
		await Promise.all([loadCase(), loadSide()]);
	} catch (e) {
		error.value = errorText(e);
	}
}

async function poll() {
	try {
		await loadRun();
		if (c.value?.status === 'analysing' || run.value?.status === 'running') await loadCase();
	} catch {
		/* keep polling */
	}
	const fast = run.value?.status === 'running' || Date.now() < watchUntil;
	timer = setTimeout(poll, fast ? 1500 : 8000);
}

async function start(kind) {
	starting.value = kind;
	error.value = '';
	try {
		await api.post(`/sep/cases/${props.id}/${kind}`);
		watchUntil = Date.now() + 20000;
		traceOpen.value = true;
		clearTimeout(timer);
		setTimeout(poll, 800);
	} catch (e) {
		error.value = errorText(e);
	} finally {
		starting.value = '';
	}
}

function startFinding(st) {
	editing.value = st.id;
	findingForm.finding = '';
	findingForm.result = 'confirmed';
}

async function saveFinding(st) {
	saving.value = true;
	try {
		await api.patch(`/items/investigation_steps/${st.id}`, { finding: findingForm.finding, result: findingForm.result });
		editing.value = null;
		await loadSide();
	} catch (e) {
		error.value = errorText(e);
	} finally {
		saving.value = false;
	}
}

async function addStep() {
	saving.value = true;
	try {
		const seq = Math.max(0, ...steps.value.map((s) => s.seq || 0)) + 1;
		await api.post('/items/investigation_steps', { case_id: props.id, seq, action: stepForm.action, finding: stepForm.finding || null, result: stepForm.result, source: 'engineer' });
		if (['new', 'triage'].includes(c.value.status)) await api.patch(`/items/cases/${props.id}`, { status: 'investigating' });
		Object.assign(stepForm, { action: '', finding: '', result: 'inconclusive' });
		await reload();
	} catch (e) {
		error.value = errorText(e);
	} finally {
		saving.value = false;
	}
}

async function resolve() {
	saving.value = true;
	try {
		await api.post(`/sep/cases/${props.id}/resolve`, {
			resolution_summary: resolveForm.summary, root_cause_id: resolveForm.root_cause_id,
			outcomes: Object.entries(resolveForm.outcomes).map(([solution_id, outcome]) => ({ solution_id, outcome })),
		});
		watchUntil = Date.now() + 30000;
		await reload();
	} catch (e) {
		error.value = errorText(e);
	} finally {
		saving.value = false;
	}
}

async function ask() {
	asking.value = true;
	const q = question.value;
	try {
		const r = await api.post(`/sep/cases/${props.id}/ask`, { question: q });
		answers.value.unshift({ q, ...r.data });
		question.value = '';
	} catch (e) {
		error.value = errorText(e);
	} finally {
		asking.value = false;
	}
}

onMounted(async () => {
	await reload();
	rootCauses.value = (await api.get('/items/root_causes', { params: { limit: -1, sort: 'category,name', fields: 'id,name,category' } })).data.data;
	watchUntil = Date.now() + 20000; // new cases: the Flow starts the analysis right after creation
	poll();
});
onBeforeUnmount(() => clearTimeout(timer));
watch(() => props.id, () => { c.value = null; reload(); });
</script>

<style scoped>
.loading { display: flex; justify-content: center; padding: 60px; }
.workspace { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1.1fr); gap: 20px; padding: 0 var(--content-padding) var(--content-padding-bottom); }
@media (max-width: 1100px) { .workspace { grid-template-columns: 1fr; } }
.col { display: flex; flex-direction: column; gap: 16px; min-width: 0; }
.panel { border: var(--theme--border-width) solid var(--theme--border-color); border-radius: var(--theme--border-radius); padding: 16px; background: var(--theme--background); display: flex; flex-direction: column; gap: 8px; }
.copilot { background: var(--theme--background-subdued); }
h2 { font-size: 16px; font-weight: 700; display: flex; align-items: center; gap: 8px; }
h3 { font-size: 14px; font-weight: 700; display: flex; align-items: center; gap: 6px; margin-top: 6px; }
label { font-weight: 600; font-size: 13px; margin-top: 4px; }
.meta { display: flex; flex-wrap: wrap; gap: 6px 16px; align-items: center; color: var(--theme--foreground-subdued); }
.status { color: var(--c); font-weight: 700; }
.status::before { content: '●'; margin-right: 6px; }
.sev { font-weight: 700; }
.sev.S1 { color: var(--theme--danger); }
.sev.S2 { color: var(--theme--warning); }
.report { white-space: pre-wrap; line-height: 1.5; }
.structured, .env-notes { font-size: 13px; display: flex; flex-direction: column; gap: 4px; background: var(--theme--background-subdued); padding: 8px 10px; border-radius: var(--theme--border-radius); }
.problem-link { color: var(--theme--danger); font-weight: 600; display: flex; gap: 6px; align-items: center; }
.steps { list-style: none; display: flex; flex-direction: column; gap: 8px; padding: 0; }
.steps li { border-left: 3px solid var(--theme--border-color); padding: 4px 10px; }
.steps li.confirmed { border-color: var(--theme--success); }
.steps li.ruled_out { border-color: var(--theme--danger); }
.steps li.inconclusive { border-color: var(--theme--warning); }
.step-head { display: flex; gap: 8px; align-items: baseline; }
.result { font-size: 12px; font-weight: 700; white-space: nowrap; color: var(--theme--foreground-subdued); }
.action { font-weight: 600; flex: 1; }
.finding { font-size: 13px; color: var(--theme--foreground-subdued); white-space: pre-wrap; }
.finding-form, .new-step { display: flex; flex-direction: column; gap: 6px; }
.new-step { border-top: var(--theme--border-width) solid var(--theme--border-color); padding-top: 10px; }
.row { display: flex; gap: 8px; align-items: center; }
.grow { flex: 1; }
.mono { font-family: var(--theme--fonts--monospace--font-family); color: var(--theme--primary); }
.applied { display: flex; gap: 10px; align-items: center; font-size: 14px; }
.muted { color: var(--theme--foreground-subdued); }
.empty { border: none !important; }
.copilot-head { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px; }
.buttons { display: flex; gap: 8px; }
.run { border: var(--theme--border-width) solid var(--theme--border-color); border-radius: var(--theme--border-radius); background: var(--theme--background); padding: 8px 10px; }
.run-head { display: flex; gap: 10px; align-items: center; cursor: pointer; font-size: 13px; flex-wrap: wrap; }
.grow-right { margin-left: auto; }
.succeeded { color: var(--theme--success); }
.failed { color: var(--theme--danger); }
.run-error { color: var(--theme--danger); font-size: 12px; margin-top: 4px; }
.trace { list-style: none; padding: 6px 0 0; margin: 0; display: flex; flex-direction: column; gap: 3px; max-height: 320px; overflow: auto; font-size: 12px; }
.trace li { display: flex; gap: 6px; align-items: baseline; }
.trace .node { font-family: var(--theme--fonts--monospace--font-family); color: var(--theme--primary); min-width: 92px; }
.trace li.guardrail .summary { color: var(--theme--warning); }
.trace li.error .summary { color: var(--theme--danger); }
.trace li.node_start .summary { font-weight: 600; }
.group { display: flex; flex-direction: column; gap: 8px; }
.hint { font-size: 12px; margin-top: -4px; }
.answer { border-top: var(--theme--border-width) solid var(--theme--border-color); padding-top: 8px; display: flex; flex-direction: column; gap: 4px; }
.q { font-weight: 600; }
.a { white-space: pre-wrap; line-height: 1.5; }
.cites { font-size: 12px; }
</style>
