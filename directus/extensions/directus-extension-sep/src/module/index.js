import { defineModule } from '@directus/extensions-sdk';
import CaseList from './CaseList.vue';
import CaseWorkspace from './CaseWorkspace.vue';
import Problems from './Problems.vue';

export default defineModule({
	id: 'sep-workspace',
	name: 'Engineer Workspace',
	icon: 'support_agent',
	routes: [
		{ path: '', component: CaseList },
		{ path: 'problems', component: Problems },
		{ path: 'cases/:id', component: CaseWorkspace, props: true },
	],
	preRegisterCheck(user) {
		return !!user;
	},
});
