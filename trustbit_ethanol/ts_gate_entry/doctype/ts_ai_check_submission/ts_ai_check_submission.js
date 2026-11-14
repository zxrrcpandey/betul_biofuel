// AI Checker submission: the form only tells the submitter where the list stands. The check runs
// in the background after Submit, so there is no button and no server call here.

frappe.ui.form.on("TS AI Check Submission", {
	refresh(frm) {
		const doc = frm.doc;
		// values from the document go into HTML, so each one is escaped first
		const esc = (value) => frappe.utils.escape_html(String(value));

		frm.set_intro(""); // also drops core's own "Submit this document to confirm"
		frm.toggle_display("sec_details", doc.docstatus !== 0);

		if (frm.is_new()) {
			frm.set_intro(
				__("Attach the payment list as Excel, CSV, PDF or a photo, then Save and Submit."),
				"blue"
			);
		} else if (doc.docstatus === 0) {
			frm.set_intro(__("Press Submit to send the list for checking."), "blue");
		} else if (doc.status === "Being checked") {
			const eta = cint((doc.__onload || {}).eta_min);
			frm.set_intro(
				eta > 0
					? __("Being checked. Usually ready in about {0} min.", [esc(eta)])
					: __("Being checked. Usually ready in a few minutes."),
				"blue"
			);
		} else if (doc.status === "Report sent") {
			frm.set_intro(
				__("Checked: {0} lines were read.", [esc(cint(doc.lines_read))]),
				"green"
			);
		} else if (doc.status === "Could not be read") {
			const note = esc(doc.status_note || "");
			frm.set_intro((note ? note + " " : "") + __("Submit a new one."), "red");
		} else if (doc.status === "Check failed") {
			// the note already says what to do
			frm.set_intro(esc(doc.status_note || ""), "red");
		}
	},
});
