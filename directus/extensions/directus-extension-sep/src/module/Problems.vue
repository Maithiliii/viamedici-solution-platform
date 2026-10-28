<template>
	<private-view title="Problems">
		<template #title-outer:prepend>
			<v-button class="header-icon" rounded disabled icon secondary><v-icon name="bug_report" /></v-button>
		</template>
		<template #navigation><module-nav /></template>
		<template #actions>
			<v-button :loading="starting" :disabled="running" @click="detect">
				<v-icon name="hub" left /> Run problem detection
			</v-button>
		</template>

		<div class="page">
			<p class="muted intro">
				Problem detection clusters <b>open cases without a solution</b> by semantic similarity and asks the agent whether they
				share a cause (ITIL problem management). It runs nightly via a Directus Flow, or on demand here.
			</p>
			<div v-if="run" class="run">
				<v-progress-circular v-if="run.status === 'running'" indeterminate x-small />
				<v-icon v-else :name="run.status === 'succeeded' ? 'check_circle' : 'error'" small />
				<span>Last detection: {{ run.status }} · {{ fmtDate(run.started_at) }} {{ fmtTime(run.started_at) }}</span>
				<span v-if="run.output?.summary" class="muted">{{ run.output.summary }}</span>
			</div>
			<v-notice v-if="error" type="danger">{{ error }}</v-notice>

			<section v-if="proposals.length">
				<h2>Proposed problems</h2>
				<div class="cards">
					<suggestion-card v-for="s in proposals" :key="s.id" :s="s" @decided="load" />
				</div>
			</section>

			<section>
				<h2>Problems</h2>
				<table class="problems">
					<thead><tr><th>Title</th><th>Status</th><th>Cases</th><th>AI</th><th>Created</th></tr></thead>
					<tbody>
						<tr v-for="p in problems" :key="p.id" @click="router.push(`/content/problems/${p.id}`)">
							<td class="title">{{ p.title }}<div class="muted desc">{{ p.description }}</div></td>
							<td>{{ p.status }}</td>
							<td>{{ p.cases?.length || 0 }}</td>
							<td><v-icon v-if="p.ai_generated" name="smart_toy" small /></td>
							<td class="muted">{{ fmtDate(p.date_created) }}</td>
						</tr>
						<tr v-if="!problems.length"><td colspan="5" class="muted empty">No problems recorded yet</td></tr>
					</tbody>
				</table>
			</section>
		</div>
	</private-view>
</template>

<script setup>
import { computed, getCurrentInstance, onBeforeUnmount, onMounted, ref } from 'vue';
import { useApi } from '@directus/extensions-sdk';
import ModuleNav from './ModuleNav.vue';
import SuggestionCard from './SuggestionCard.vue';
import { errorText, fmtDate, fmtTime } from './util.js';

const api = useApi();
const router = getCurrentInstance().proxy.$router;
const proposals = ref([]);
const problems = ref([]);
const run = ref(null);
const starting = ref(false);
const error = ref('');
let timer = null;

const running = computed(() => run.value?.status === 'running');

async function load() {
	try {
		const [s, p, r] = await Promise.all([
			api.get('/items/ai_suggestions', { params: { filter: { type: { _eq: 'problem' }, status: { _eq: 'pending' } }, limit: -1, sort: '-date_created' } }),
			api.get('/items/problems', { params: { limit: -1, sort: '-date_created', fields: '*,cases.id' } }),
			api.get('/items/agent_runs', { params: { filter: { graph: { _eq: 'problem_detection' } }, sort: '-started_at', limit: 1 } }),
		]);
		proposals.value = s.data.data;
		problems.value = p.data.data;
		run.value = r.data.data[0] || null;
	} catch (e) {
		error.value = errorText(e);
	}
}

async function poll() {
	await load();
	timer = setTimeout(poll, running.value ? 2000 : 15000);
}

async function detect() {
	starting.value = true;
	try {
		await api.post('/sep/problems/detect');
		clearTimeout(timer);
		setTimeout(poll, 1000);
	} catch (e) {
		error.value = errorText(e);
	} finally {
		starting.value = false;
	}
}

onMounted(poll);
onBeforeUnmount(() => clearTimeout(timer));
</script>

<style scoped>
.page { padding: 0 var(--content-padding) var(--content-padding-bottom); display: flex; flex-direction: column; gap: 20px; max-width: 1100px; }
.intro { max-width: 800px; }
.run { display: flex; gap: 10px; align-items: center; font-size: 13px; }
h2 { font-size: 16px; font-weight: 700; margin-bottom: 10px; }
.cards { display: flex; flex-direction: column; gap: 12px; }
table.problems { width: 100%; border-collapse: collapse; }
.problems th { text-align: left; color: var(--theme--foreground-subdued); padding: 8px; border-bottom: var(--theme--border-width) solid var(--theme--border-color); }
.problems td { padding: 10px 8px; border-bottom: var(--theme--border-width) solid var(--theme--border-color); vertical-align: top; }
.problems tbody tr { cursor: pointer; }
.problems tbody tr:hover { background: var(--theme--background-subdued); }
.title { font-weight: 600; }
.desc { font-weight: 400; font-size: 12px; white-space: pre-wrap; max-height: 3.6em; overflow: hidden; }
.muted { color: var(--theme--foreground-subdued); }
.empty { text-align: center; }
</style>
