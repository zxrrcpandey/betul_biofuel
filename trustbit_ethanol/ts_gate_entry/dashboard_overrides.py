def get_data_for_purchase_order(data):
	"""Add TS Gate Entry system documents to Purchase Order connections panel."""
	data.setdefault("transactions", []).append({
		"label": "TS Gate Entry",
		"items": [
			"TS Gate Entry",
			"TS Weighbridge Log",
			"TS Quality Inspection",
			"TS Deduction Sheet",
			"TS Material Inspection",
		]
	})
	return data


def get_data_for_request_for_quotation(data):
	"""Link the source Material Request into the RFQ connections panel.

	RFQ Item holds the link (material_request), not the other way round, so this
	is an internal_link — the same mechanism erpnext already uses on Purchase
	Order. Both keys are required: "internal_links" alone renders nothing, and a
	"transactions" entry alone would fall through to get_external_links and 500
	on a column Material Request does not have.

	docstatus is popped because core's RFQ dashboard pins it to 1, which hides
	the WHOLE connections panel on drafts — the point where the source MR is
	most worth checking.

	Must stay a pure function of `data`: the result is cached per-doctype in a
	site-global meta pickle, so anything user-dependent here leaks across users.
	"""
	data.pop("docstatus", None)
	data.setdefault("internal_links", {})["Material Request"] = ["items", "material_request"]
	data.setdefault("transactions", []).append({
		"label": "Reference",
		"items": ["Material Request"]
	})
	return data
