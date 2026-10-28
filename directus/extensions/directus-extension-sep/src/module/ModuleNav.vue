<template>
	<v-list nav>
		<v-list-item to="/sep-workspace" exact clickable>
			<v-list-item-icon><v-icon name="inbox" /></v-list-item-icon>
			<v-list-item-content><v-text-overflow text="Cases" /></v-list-item-content>
		</v-list-item>
		<v-list-item to="/sep-workspace/problems" clickable>
			<v-list-item-icon><v-icon name="bug_report" /></v-list-item-icon>
			<v-list-item-content><v-text-overflow text="Problems" /></v-list-item-content>
		</v-list-item>
		<v-divider />
		<v-list-item to="/content/solutions" clickable>
			<v-list-item-icon><v-icon name="menu_book" /></v-list-item-icon>
			<v-list-item-content><v-text-overflow text="Knowledge base" /></v-list-item-content>
		</v-list-item>
		<v-list-item to="/insights" clickable>
			<v-list-item-icon><v-icon name="insights" /></v-list-item-icon>
			<v-list-item-content><v-text-overflow text="Dashboard" /></v-list-item-content>
		</v-list-item>
	</v-list>
</template>
