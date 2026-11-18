// AI Checker submission: the form tells the submitter where the list stands and, once it is checked,
// the points they can fix themselves. The check runs in the background after Submit, so there is no
// button and no server call here: the points come with the form (only to the person who submitted).

// The words come from the file and the ERP, so every one is set as text, never as HTML. The section
// is a "custom" dashboard section, which the form drops by itself on every refresh. Its second class
// gives it an open/closed memory of its own (the form keeps that per class, and "custom" is shared
// with other forms).
function show_own_points(frm, points) {
	if (!Array.isArray(points)) return;
	const mst = frm.doc.check_type === "MST Report";
	const $body = $(frm.dashboard.add_section("", __("Points for you to check"), "custom ts-own-points"));
	const note = __("The MD gets the full report.");
	if (!points.length) {
		$("<div class='text-muted'>")
			.text(__("No point for you to correct.") + " " + note)
			.appendTo($body);
	} else {
		// two columns only, so the list reads the same on a phone: the line, then everything about it
		const $table = $("<table class='table table-bordered' style='margin-bottom: 8px;'>").appendTo($body);
		const $head = $("<tr>").appendTo($("<thead>").appendTo($table));
		$("<th style='width: 1%; white-space: nowrap;'>").text(__("Line")).appendTo($head);
		$("<th>").text(__("Point")).appendTo($head);
		const $rows = $("<tbody>").appendTo($table);
		points.forEach((p) => {
			const $row = $("<tr>").appendTo($rows);
			$("<td style='white-space: nowrap;'>")
				.text(cint(p.line_no) ? p.line_no : mst ? __("Sheet or file") : __("Whole list"))
				.appendTo($row);
			const $point = $("<td>").appendTo($row);
			const $title = $("<div>").appendTo($point);
			$("<span class='indicator-pill' style='margin-right: 6px;'>")
				.addClass(p.level === "Serious" ? "red" : "orange")
				.text(__(p.level))
				.appendTo($title);
			$("<b>").text(p.title || "").appendTo($title);
			const on_list = [p.supplier, p.amount].filter(Boolean).join(" · ");
			if (cint(p.line_no) && on_list) {
				$("<div class='small' style='margin-top: 4px;'>").text(on_list).appendTo($point);
			}
			$("<div class='text-muted' style='margin-top: 4px;'>").text(p.detail || "").appendTo($point);
		});
		$("<div class='text-muted small'>")
			.text(
					(mst
						? __("Correct these in the workbook and submit it again if needed.")
						: __("Correct these and submit the list again if needed.")) +
						" " +
						note
				)
			.appendTo($body);
	}
	frm.dashboard.show();
}

// The form serves two kinds of file. The labels and help of the date and the file follow the kind chosen;
// the payment list's own words are the ones in the DocType.
function show_type_words(frm) {
	const mst = frm.doc.check_type === "MST Report";
	if (!frm._ts_words) {
		frm._ts_words = ["list_date", "file"].map((f) => [f, frm.fields_dict[f].df.label, frm.fields_dict[f].df.description || ""]);
	}
	const words = mst
		? [
				["list_date", __("Month of the report"), __("Any date in that month.")],
				["file", __("MST report file"), __("The MST workbook as Excel: all its sheets, or any one sheet. One file per submission.")],
		  ]
		: frm._ts_words;
	words.forEach(([fieldname, label, description]) => {
		frm.set_df_property(fieldname, "label", label);
		frm.set_df_property(fieldname, "description", description);
	});
}

frappe.ui.form.on("TS AI Check Submission", {
	check_type(frm) {
		frm.events.refresh(frm);
	},

	onload(frm) {
		// a payment list is sent by Accounts only (the server refuses it from anyone else): a new form of
		// another person starts as an MST report
		if (frm.is_new() && !frappe.user.has_role(["Accounts User", "Accounts Manager"])) {
			frm.set_value("check_type", "MST Report");
		}
	},

	refresh(frm) {
		const doc = frm.doc;
		const mst = doc.check_type === "MST Report";
		show_type_words(frm);
		// values from the document go into HTML, so each one is escaped first
		const esc = (value) => frappe.utils.escape_html(String(value));

		frm.set_intro(""); // also drops core's own "Submit this document to confirm"
		frm.toggle_display("sec_details", doc.docstatus !== 0);

		if (frm.is_new()) {
			frm.set_intro(
				mst
					? __("Attach the MST report as Excel (all its sheets, or any one sheet), then Save and Submit.")
					: __("Attach the payment list as Excel, CSV, PDF or a photo, then Save and Submit."),
				"blue"
			);
		} else if (doc.docstatus === 0) {
			frm.set_intro(
				mst ? __("Press Submit to send the report for checking.") : __("Press Submit to send the list for checking."),
				"blue"
			);
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
			show_own_points(frm, (doc.__onload || {}).own_points);
		} else if (doc.status === "Could not be read") {
			const note = esc(doc.status_note || "");
			frm.set_intro((note ? note + " " : "") + __("Submit a new one."), "red");
		} else if (doc.status === "Check failed") {
			// the note already says what to do
			frm.set_intro(esc(doc.status_note || ""), "red");
		}
	},
});
