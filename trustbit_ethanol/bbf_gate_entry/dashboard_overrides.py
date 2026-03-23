def get_data_for_purchase_order(data):
	"""Add BBF Gate Entry system documents to Purchase Order connections panel."""
	data.setdefault("transactions", []).append({
		"label": "BBF Gate Entry",
		"items": [
			"BBF Gate Entry",
			"BBF Weighbridge Log",
			"BBF Quality Inspection",
			"BBF Deduction Sheet",
			"BBF Material Inspection",
		]
	})
	return data
