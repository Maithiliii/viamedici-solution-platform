export const STATUS_LABEL = {
	new: 'New', analysing: 'AI analysing', triage: 'Triage', waiting_customer: 'Waiting for customer',
	investigating: 'Investigating', resolved: 'Resolved', knowledge_review: 'Knowledge review', closed: 'Closed',
};

export const STATUS_COLOR = {
	new: 'var(--theme--primary)', analysing: 'var(--theme--primary)', triage: 'var(--theme--warning)',
	waiting_customer: 'var(--theme--foreground-subdued)', investigating: 'var(--theme--warning)',
	resolved: 'var(--theme--success)', knowledge_review: 'var(--theme--secondary, #9f7aea)', closed: 'var(--theme--foreground-subdued)',
};

export const CONF_COLOR = { high: 'var(--theme--success)', medium: 'var(--theme--warning)', low: 'var(--theme--foreground-subdued)' };

export const VERDICT = {
	applies: { label: 'Applies', color: 'var(--theme--success)' },
	applies_with_changes: { label: 'Applies with changes', color: 'var(--theme--warning)' },
	not_applicable: { label: 'Not applicable', color: 'var(--theme--danger)' },
	unknown: { label: 'Unclear', color: 'var(--theme--foreground-subdued)' },
};

export function fmtDate(v) {
	if (!v) return '';
	return new Date(v).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
}

export function fmtTime(v) {
	if (!v) return '';
	return new Date(v).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

export function errorText(e) {
	return e?.response?.data?.error || e?.response?.data?.errors?.[0]?.message || e?.message || String(e);
}
