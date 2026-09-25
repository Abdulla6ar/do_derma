(() => {
	if (typeof frappe !== "undefined" && frappe.provide) {
		frappe.provide("do_derma");
	} else {
		window.do_derma = window.do_derma || {};
	}

	const translate = (...args) => (typeof __ === "function" ? __(...args) : args[0]);

	do_derma.openChart = async function ({ patient } = {}) {
		const context = patient || window.doHealthSidebar?.getSelectedPatient?.() || window.do_health?.patientWatcher?.read?.();
		const patientId = context?.patient;
		if (!patientId) {
			frappe.msgprint(translate("Select a patient first."));
			return;
		}

		try {
			const { message } = await frappe.call({
				method: "do_derma.api.ensure_chart_context",
				args: {
					patient: patientId,
					appointment: context?.appointment,
					encounter: context?.encounter_name || context?.encounter,
				},
			});

			frappe.route_options = {
				patient: message?.patient || patientId,
				appointment: message?.appointment || context?.appointment,
				encounter: message?.encounter || context?.encounter_name || context?.encounter,
			};
			return { route: ["derma-chart"] };
		} catch (error) {
			console.warn("[do_derma] Failed to open chart", error);
			frappe.msgprint({
				title: translate("Derma Chart"),
				message: translate("Unable to open the dermatology chart."),
				indicator: "red",
			});
		}
	};

	// do_health renders these in health_sidebar.js renderVisitHistoryCardDetail; renamed there, the button just disappears.
	const VISIT_HISTORY = {
		drawer: "#do-health-panel-drawer",
		actions: ".visit-history-actions",
		encounterButton: '[data-open-visit-history-route="Patient Encounter"]',
		chartButton: "[data-open-derma-chart]",
	};

	function addChartButtons(root) {
		if (!root?.querySelectorAll) return;
		// The observer watches all of document.body, so most mutations are unrelated
		// to Visit History; skip the querySelectorAll unless root is in the drawer.
		if (root.id !== "do-health-panel-drawer" && !root.closest?.(VISIT_HISTORY.drawer)) return;
		root.querySelectorAll(VISIT_HISTORY.actions).forEach((actions) => {
			const encounterButton = actions.querySelector(VISIT_HISTORY.encounterButton);
			if (!encounterButton || actions.querySelector(VISIT_HISTORY.chartButton)) return;
			const button = document.createElement("button");
			button.type = "button";
			button.dataset.openDermaChart = encounterButton.dataset.docname;
			button.textContent = translate("Open Derma Chart");
			encounterButton.after(button);
		});
	}

	function watchVisitHistoryDrawer() {
		new MutationObserver((mutations) => {
			mutations.forEach((mutation) => mutation.addedNodes.forEach((node) => node.nodeType === 1 && addChartButtons(node.parentElement || node)));
		}).observe(document.body, { childList: true, subtree: true });
	}

	if (document.body) {
		watchVisitHistoryDrawer();
	} else {
		frappe.ready(watchVisitHistoryDrawer);
	}

	let openingChart = false;
	document.addEventListener("click", async (event) => {
		const button = event.target.closest?.(VISIT_HISTORY.chartButton);
		if (!button || openingChart) return;
		openingChart = true;
		try {
			const selected = window.doHealthSidebar?.getSelectedPatient?.() || {};
			const result = await do_derma.openChart({
				patient: { patient: selected.patient, encounter: button.dataset.openDermaChart },
			});
			if (result?.route) frappe.set_route(...result.route);
		} finally {
			openingChart = false;
		}
	});
})();
