<template>
	<private-view title="Engineer Workspace">
		<template #title-outer:prepend>
			<v-button class="header-icon" rounded disabled icon secondary><v-icon name="support_agent" /></v-button>
		</template>
		<template #navigation><module-nav /></template>
		<template #actions>
			<v-button rounded icon v-tooltip.bottom="'New case'" @click="newCase.open = true"><v-icon name="add" /></v-button>
		</template>

		<div class="page">
			<section class="search">
				<v-input v-model="query" placeholder="Search the knowledge base (cases and solutions, hybrid search)…" @keyup.enter="search">
					<template #prepend><v-icon name="search" /></template>
					<template #append><v-progress-circular v-if="searching" indeterminate small /></template>
				</v-input>
				<div v-if="hits.length" class="hits">
					<div v-for="h in hits" :key="h.id" class="hit">
						<router-link v-if="h.type === 'case'" :to="`/sep-workspace/cases/${h.id}`" class="num">{{ h.number }}</router-link>
						<router-link v-else :to="`/content/solutions/${h.id}`" class="num">{{ h.number }}</router-link>
						<div class="hit-body">
							<div class="hit-title">{{ h.title }}</div>
							<div class="hit-meta">
								<span>{{ h.type }}</span>
								<span v-if="h.valid_versions">versions {{ h.valid_versions }}</span>
								<span v-if="h.track_record">{{ h.track_record }}</span>
								<span>score {{ Number(h.score).toFixed(4) }}</span>
							</div>
						</div>
					</div>
					<v-button x-small secondary @click="hits = []">Close results</v-button>
				</div>
			</section>

			<div class="filters">
				<v-chip v-for="f in FILTERS" :key="f.key" :class="{ active: filter === f.key }" clickable small @click="filter = f.key">
					{{ f.label }} <span class="count">{{ counts[f.key] }}</span>
				</v-chip>
			</div>

			<v-notice v-if="error" type="danger">{{ error }}</v-notice>
			<div v-if="loading" class="loading"><v-progress-circular indeterminate /></div>
			<table v-else class="cases">
				<thead>
					<tr><th>Case</th><th>Title</th><th>Customer</th><th>Version</th><th>Module</th><th>Sev.</th><th>Status</th><th>Opened</th></tr>
				</thead>
				<tbody>
					<tr v-for="c in visible" :key="c.id" @click="open(c.id)">
						<td class="mono">{{ c.case_number }}</td>
						<td class="title">{{ c.title }}</td>
						<td>{{ c.environment_id?.customer_id?.name }}</td>
						<td>{{ c.epim_version }}</td>
						<td>{{ c.module_id?.code }}</td>
						<td><span :class="['sev', c.severity]">{{ c.severity }}</span></td>
						<td><span class="status" :style="{ '--c': STATUS_COLOR[c.status] }">{{ STATUS_LABEL[c.status] }}</span></td>
						<td class="muted">{{ fmtDate(c.opened_at) }}</td>
					</tr>
					<tr v-if="!visible.length"><td colspan="8" class="muted empty">No cases</td></tr>
				</tbody>
			</table>
		</div>

		<v-dialog v-model="newCase.open" @esc="newCase.open = false">
			<v-card class="new-case">
				<v-card-title>New customer case</v-card-title>
				<v-card-text>
					<div class="form">
						<label>Customer environment</label>
						<v-select v-model="newCase.environment" :items="environmentItems" placeholder="Select customer" />
						<label>Title</label>
						<v-input v-model="newCase.title" placeholder="Short summary in the customer's words" />
						<label>Customer report</label>
						<v-textarea v-model="newCase.report" placeholder="Paste the customer's message" />
						<label>Severity</label>
						<v-select v-model="newCase.severity" :items="['S1', 'S2', 'S3', 'S4'].map((s) => ({ text: s, value: s }))" />
					</div>
				</v-card-text>
				<v-card-actions>
					<v-button secondary @click="newCase.open = false">Cancel</v-button>
					<v-button :loading="newCase.saving" :disabled="!newCase.environment || !newCase.title || !newCase.report" @click="createCase">
						Create case
					</v-button>
				</v-card-actions>
			</v-card>
		</v-dialog>
	</private-view>
</template>

<script setup>
import { computed, getCurrentInstance, onMounted, reactive, ref } from 'vue';
import { useApi } from '@directus/extensions-sdk';
import ModuleNav from './ModuleNav.vue';
import { STATUS_COLOR, STATUS_LABEL, errorText, fmtDate } from './util.js';

const api = useApi();
const router = getCurrentInstance().proxy.$router;

const FILTERS = [
	{ key: 'active', label: 'Active' },
	{ key: 'review', label: 'Knowledge review' },
	{ key: 'mine', label: 'Assigned to me' },
	{ key: 'all', label: 'All' },
];
const ACTIVE = ['new', 'analysing', 'triage', 'waiting_customer', 'investigating', 'resolved'];

const cases = ref([]);
const loading = ref(true);
const error = ref('');
const filter = ref('active');
const me = ref(null);
const environments = ref([]);
const query = ref('');
const hits = ref([]);
const searching = ref(false);
const newCase = reactive({ open: false, saving: false, environment: null, title: '', report: '', severity: 'S3' });

const match = {
	active: (c) => ACTIVE.includes(c.status),
	review: (c) => c.status === 'knowledge_review' || c.status === 'resolved',
	mine: (c) => c.assigned_to === me.value && c.status !== 'closed',
	all: () => true,
};
const visible = computed(() => cases.value.filter(match[filter.value]));
const counts = computed(() => Object.fromEntries(FILTERS.map((f) => [f.key, cases.value.filter(match[f.key]).length])));
const environmentItems = computed(() => environments.value.map((e) => ({
	text: `${e.customer_id?.name} (${e.name}, EPIM ${e.release_id?.version})`, value: e.id,
})));

async function load() {
	loading.value = true;
	try {
		const [c, u, e] = await Promise.all([
			api.get('/items/cases', { params: { limit: -1, sort: '-opened_at', fields: 'id,case_number,title,status,severity,epim_version,opened_at,assigned_to,module_id.code,environment_id.customer_id.name' } }),
			api.get('/users/me', { params: { fields: 'id' } }),
			api.get('/items/environments', { params: { limit: -1, fields: 'id,name,customer_id.name,release_id.version', sort: 'customer_id.name' } }),
		]);
		cases.value = c.data.data;
		me.value = u.data.data.id;
		environments.value = e.data.data;
	} catch (e) {
		error.value = errorText(e);
	} finally {
		loading.value = false;
	}
}

function open(id) {
	router.push(`/sep-workspace/cases/${id}`);
}

async function search() {
	if (!query.value.trim()) return;
	searching.value = true;
	try {
		hits.value = (await api.get('/sep/search', { params: { q: query.value } })).data;
	} catch (e) {
		error.value = errorText(e);
	} finally {
		searching.value = false;
	}
}

async function createCase() {
	newCase.saving = true;
	try {
		const env = environments.value.find((e) => e.id === newCase.environment);
		const res = await api.post('/items/cases', {
			environment_id: newCase.environment, title: newCase.title, customer_report: newCase.report,
			severity: newCase.severity, epim_version: env?.release_id?.version, status: 'new', channel: 'portal', assigned_to: me.value,
		});
		newCase.open = false;
		open(res.data.data.id);
	} catch (e) {
		error.value = errorText(e);
	} finally {
		newCase.saving = false;
	}
}

onMounted(load);
</script>

<style scoped>
.page { padding: 0 var(--content-padding) var(--content-padding-bottom); display: flex; flex-direction: column; gap: 16px; }
.search { max-width: 900px; }
.hits { margin-top: 8px; border: var(--theme--border-width) solid var(--theme--border-color); border-radius: var(--theme--border-radius); padding: 8px 12px; display: flex; flex-direction: column; gap: 8px; }
.hit { display: flex; gap: 12px; }
.num { font-family: var(--theme--fonts--monospace--font-family); color: var(--theme--primary); min-width: 72px; }
.hit-title { font-weight: 600; }
.hit-meta { display: flex; gap: 12px; color: var(--theme--foreground-subdued); font-size: 12px; }
.filters { display: flex; gap: 8px; flex-wrap: wrap; }
.filters .active { --v-chip-background-color: var(--theme--primary); --v-chip-color: var(--theme--foreground-inverted, #fff); }
.count { opacity: 0.7; margin-left: 4px; }
.loading { display: flex; justify-content: center; padding: 40px; }
table.cases { width: 100%; border-collapse: collapse; }
.cases th { text-align: left; font-weight: 600; color: var(--theme--foreground-subdued); padding: 8px; border-bottom: var(--theme--border-width) solid var(--theme--border-color); }
.cases td { padding: 10px 8px; border-bottom: var(--theme--border-width) solid var(--theme--border-color-subdued, var(--theme--border-color)); }
.cases tbody tr { cursor: pointer; }
.cases tbody tr:hover { background: var(--theme--background-subdued); }
.mono { font-family: var(--theme--fonts--monospace--font-family); white-space: nowrap; }
.title { font-weight: 500; }
.muted { color: var(--theme--foreground-subdued); }
.empty { text-align: center; padding: 24px; }
.status { color: var(--c); font-weight: 600; white-space: nowrap; }
.status::before { content: '●'; margin-right: 6px; }
.sev { font-weight: 700; }
.sev.S1 { color: var(--theme--danger); }
.sev.S2 { color: var(--theme--warning); }
.new-case { min-width: min(640px, 90vw); }
.form { display: flex; flex-direction: column; gap: 8px; }
.form label { font-weight: 600; margin-top: 8px; }
.hint { color: var(--theme--foreground-subdued); font-size: 13px; }
</style>
