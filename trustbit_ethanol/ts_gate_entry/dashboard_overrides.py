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
