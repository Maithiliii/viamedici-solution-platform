<template>
	<div :class="['card', s.status]">
		<div class="head">
			<span v-if="s.rank" class="rank">#{{ s.rank }}</span>
			<div class="title">{{ s.title }}</div>
			<span v-if="verdict" class="badge" :style="{ '--c': verdict.color }">{{ verdict.label }}</span>
			<span v-else class="badge" :style="{ '--c': CONF_COLOR[s.confidence] }">{{ s.confidence }}</span>
		</div>

		<p v-if="s.rationale" class="rationale">{{ s.rationale }}</p>

		<!-- solution details: why it does / does not apply -->
		<template v-if="s.type === 'solution'">
			<p v-if="p.verdict_reason" class="detail"><b>Applicability:</b> {{ p.verdict_reason }}</p>
			<p v-if="p.adaptations" class="detail"><b>Adapt:</b> {{ p.adaptations }}</p>
			<div class="facts">
				<span v-if="p.valid_versions">Valid for {{ p.valid_versions }}</span>
				<span v-if="p.track_record">Track record: {{ p.track_record.resolved }}/{{ p.track_record.applied }} resolved<template v-if="p.track_record.failed">, {{ p.track_record.failed }} failed</template></span>
			</div>
			<div v-if="p.track_record?.by_major_version?.length" class="facts">
				<span v-for="v in p.track_record.by_major_version" :key="v.major">{{ v.major }}: {{ v.resolved }}✓ {{ v.failed }}✗</span>
			</div>
			<p v-for="h in p.history_with_this_customer || []" :key="h.case" class="detail warn">
				Used before at this customer: {{ h.case }} ({{ h.version }}) → <b>{{ h.outcome }}</b>
			</p>
			<p v-for="c in p.release_changes_since_proven || []" :key="c.title" class="detail warn">
				Release {{ c.version }} [{{ c.type }}]: {{ c.title }}
			</p>
		</template>

		<p v-if="s.type === 'hypothesis' && p.confirm_by" class="detail"><b>Confirm by:</b> {{ p.confirm_by }}</p>
		<p v-if="s.type === 'next_step' && p.expected_finding" class="detail"><b>Expected finding:</b> {{ p.expected_finding }}</p>

		<template v-if="s.type === 'clarification'">
			<ul class="list"><li v-for="m in p.missing_info || []" :key="m.question">{{ m.question }} <span class="muted">({{ m.why }})</span></li></ul>
			<pre v-if="p.message" class="message">{{ p.message }}</pre>
		</template>

		<template v-if="s.type === 'case_structure'">
			<div class="facts">
				<span>Module {{ p.module_code }}</span><span>Severity {{ p.severity_suggestion }}</span>
				<span v-if="p.suspected_trigger">Trigger: {{ p.suspected_trigger }}</span>
			</div>
			<p v-if="p.symptoms?.length" class="detail"><b>Symptoms:</b> {{ p.symptoms.join('; ') }}</p>
			<p v-if="p.error_codes?.length" class="detail"><b>Errors:</b> {{ p.error_codes.join(', ') }}</p>
		</template>

		<!-- knowledge capture: editable KCS draft -->
		<template v-if="s.type === 'knowledge_create' || (s.type === 'knowledge_update' && p.solution)">
			<div v-if="s.status === 'pending'" class="draft">
				<label>Title</label><v-input v-model="draft.title" />
				<label>Issue</label><v-textarea v-model="draft.issue" />
				<label>Environment</label><v-textarea v-model="draft.environment_conditions" />
				<label>Cause</label><v-textarea v-model="draft.cause" />
				<label>Resolution steps</label><v-textarea v-model="draft.resolution_steps" />
				<label>Verification</label><v-textarea v-model="draft.verification_steps" />
				<div class="row">
					<div><label>Min version</label><v-input v-model="draft.min_version" /></div>
					<div><label>Max version</label><v-input v-model="draft.max_version" /></div>
				</div>
			</div>
		</template>
		<p v-if="s.type === 'problem'" class="detail">
			<b>Cases:</b> {{ (p.case_numbers || []).join(', ') }}<br />
			<b>Suspected cause:</b> {{ p.suspected_cause }}<br />
			<template v-if="p.workaround"><b>Workaround:</b> {{ p.workaround }}<br /></template>
			<template v-if="p.related_release_change"><b>Release change:</b> {{ p.related_release_change }}</template>
		</p>

		<div v-if="s.citations?.length" class="cites">
			<span class="muted">Sources:</span>
			<router-link v-for="c in s.citations" :key="c.number" :to="linkFor(c)" class="cite">{{ c.number }}</router-link>
		</div>

		<div v-if="s.status === 'pending'" class="actions">
			<template v-if="!rejecting">
				<v-button small :loading="busy" @click="decide('accept')">
					<v-icon name="check" left small /> {{ acceptLabel }}
				</v-button>
				<v-button small secondary :disabled="busy" @click="rejecting = true"><v-icon name="close" left small /> Reject</v-button>
			</template>
			<template v-else>
				<v-input v-model="note" small placeholder="Why? (feedback improves the agents)" class="note" autofocus />
				<v-button small kind="danger" :loading="busy" @click="decide('reject')">Reject</v-button>
				<v-button small secondary @click="rejecting = false">Cancel</v-button>
			</template>
		</div>
		<div v-else class="decided muted">
			{{ s.status }}<template v-if="s.feedback_note"> - "{{ s.feedback_note }}"</template>
		</div>
		<v-notice v-if="error" type="danger" class="err">{{ error }}</v-notice>
	</div>
</template>

<script setup>
import { computed, reactive, ref } from 'vue';
import { useApi } from '@directus/extensions-sdk';
import { CONF_COLOR, VERDICT, errorText } from './util.js';

const props = defineProps({ s: { type: Object, required: true } });
const emit = defineEmits(['decided']);
const api = useApi();

const p = computed(() => props.s.payload || {});
const verdict = computed(() => (props.s.type === 'solution' ? VERDICT[p.value.verdict] : null));
const draft = reactive({ ...(p.value.solution || {}) });
const busy = ref(false);
const rejecting = ref(false);
const note = ref('');
const error = ref('');

const acceptLabel = computed(() => ({
	case_structure: 'Apply to case', clarification: 'Ask customer', next_step: 'Add to log', hypothesis: 'Investigate',
	solution: 'Reference', similar_case: 'Link case', knowledge_link: 'Approve link', knowledge_update: 'Approve update',
	knowledge_create: 'Publish solution', problem: 'Create problem',
}[props.s.type] || 'Accept'));

function linkFor(c) {
	return c.type === 'case' && c.id ? `/sep-workspace/cases/${c.id}` : `/content/${c.type === 'case' ? 'cases' : 'solutions'}?search=${c.number}`;
}

async function decide(decision) {
	busy.value = true;
	error.value = '';
	try {
		const edits = props.s.type.startsWith('knowledge_') && p.value.solution ? { ...draft } : {};
		await api.post(`/sep/suggestions/${props.s.id}/decide`, { decision, note: note.value, edits });
		emit('decided');
	} catch (e) {
		error.value = errorText(e);
	} finally {
		busy.value = false;
		rejecting.value = false;
	}
}
</script>

<style scoped>
.card { border: var(--theme--border-width) solid var(--theme--border-color); border-radius: var(--theme--border-radius); padding: 12px 14px; display: flex; flex-direction: column; gap: 6px; background: var(--theme--background); }
.card.accepted { border-color: var(--theme--success); }
.card.rejected { opacity: 0.6; }
.head { display: flex; align-items: flex-start; gap: 8px; }
.rank { font-weight: 700; color: var(--theme--foreground-subdued); }
.title { font-weight: 600; flex: 1; }
.badge { white-space: nowrap; font-size: 12px; font-weight: 700; color: var(--c); border: 1px solid var(--c); border-radius: 12px; padding: 0 8px; text-transform: capitalize; }
.rationale { color: var(--theme--foreground); }
.detail { font-size: 13px; }
.detail.warn { color: var(--theme--warning); }
.facts { display: flex; flex-wrap: wrap; gap: 6px 14px; font-size: 12px; color: var(--theme--foreground-subdued); }
.list { margin-left: 18px; font-size: 13px; }
.message { white-space: pre-wrap; font-family: inherit; font-size: 13px; background: var(--theme--background-subdued); padding: 8px; border-radius: var(--theme--border-radius); }
.muted { color: var(--theme--foreground-subdued); }
.cites { display: flex; gap: 8px; flex-wrap: wrap; font-size: 12px; align-items: center; }
.cite { font-family: var(--theme--fonts--monospace--font-family); color: var(--theme--primary); }
.actions { display: flex; gap: 8px; align-items: center; margin-top: 4px; }
.note { flex: 1; }
.decided { font-size: 12px; text-transform: capitalize; }
.draft { display: flex; flex-direction: column; gap: 4px; }
.draft label { font-size: 12px; font-weight: 600; margin-top: 4px; }
.row { display: flex; gap: 12px; }
.row > div { flex: 1; }
.err { margin-top: 6px; }
</style>
