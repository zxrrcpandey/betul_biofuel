import frappe
from frappe.model.document import Document


class TSAssetTransaction(Document):
	def after_insert(self):
		"""Auto-complete most transaction types on save. Discard requires approval."""
		if self.transaction_type == "Discard":
			# Discard needs approval — stay Draft, user clicks "Submit for Approval"
			return

		# All other types: auto-complete immediately
		from trustbit_ethanol.ts_asset_tracker.ts_asset_api import complete_transaction
		complete_transaction(self.name)
