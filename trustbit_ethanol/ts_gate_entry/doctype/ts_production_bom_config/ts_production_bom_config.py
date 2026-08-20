import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, escape_html as esc


class TSProductionBOMConfig(Document):
	def validate(self):
		"""Row-level config checks. NOT auto-invoked on a parent Settings save —
		Frappe does not run child controllers there; TSSettings._validate_bom_configs()
		calls this explicitly for every row. Keep it that way or the checks go inert."""
		if not (self.source_warehouse or self.cost_center
		        or self.get("releaser_user") or self.get("releaser_role")):
			frappe.throw(
				_("Row #{0}: set a Source Warehouse, Cost Center and/or Releaser for "
				  "BOM {1} — a row with none has no effect.").format(
					self.idx, esc(self.bom or "")),
				title=_("Empty BOM Config Row"))

		bom_company = frappe.db.get_value("BOM", self.bom, "company")

		if self.source_warehouse:
			wh = frappe.db.get_value(
				"Warehouse", self.source_warehouse, ["is_group", "company"], as_dict=True)
			if wh and cint(wh.is_group):
				frappe.throw(
					_("Row #{0}: '{1}' is a GROUP warehouse — pick a leaf warehouse "
					  "stock can actually sit in.").format(self.idx, esc(self.source_warehouse)),
					title=_("Group Warehouse Not Allowed"))
			if wh and wh.company and bom_company and wh.company != bom_company:
				frappe.throw(
					_("Row #{0}: warehouse '{1}' belongs to company '{2}', but BOM {3} "
					  "belongs to '{4}'.").format(
						self.idx, esc(self.source_warehouse), esc(wh.company), esc(self.bom), esc(bom_company or "")),
					title=_("Warehouse Company Mismatch"))
			# A department-claimed warehouse would push EVERY release row of this BOM's
			# runs into that department's release slot and leave the Store Manager with
			# nothing to release — the department gate exists per-item, not per-BOM.
			from trustbit_ethanol.ts_gate_entry.ts_production_dept_release import (
				_warehouse_to_category)
			cat = _warehouse_to_category().get(self.source_warehouse)
			if cat:
				frappe.throw(
					_("Row #{0}: warehouse '{1}' is claimed by department category '{2}' "
					  "(Release Warehouses). A BOM-wide source there would route the whole "
					  "release through that department. For department-sourced items, set "
					  "the warehouse on the run's material rows instead.").format(
						self.idx, esc(self.source_warehouse), esc(cat)),
					title=_("Department-Claimed Warehouse"))
			# The WIP warehouse can never be the release SOURCE — the release moves
			# stock source -> WIP, so a same-warehouse pair fails at SE insert and
			# the surplus auto-return silently no-ops (wip == dest early return).
			wip = frappe.db.get_single_value("TS Settings", "ts_production_wip_warehouse")
			if wip and self.source_warehouse == wip:
				frappe.throw(
					_("Row #{0}: '{1}' is the WIP warehouse — the release moves stock "
					  "FROM the source INTO WIP, so they cannot be the same.").format(
						self.idx, esc(self.source_warehouse)),
					title=_("Source Cannot Be WIP"))

		if self.get("releaser_user"):
			u = frappe.db.get_value(
				"User", self.releaser_user, ["enabled", "user_type"], as_dict=True)
			if not u or not cint(u.enabled) or u.user_type != "System User":
				frappe.throw(
					_("Row #{0}: releaser '{1}' must be an ENABLED System User.").format(
						self.idx, esc(self.releaser_user)),
					title=_("Invalid Releaser"))
			# Warning only: the gate itself is in approve_release; without write
			# access on the run the releaser hits a runtime PermissionError there.
			if not frappe.has_permission("TS Production Entry", "write",
			                             user=self.releaser_user):
				frappe.msgprint(
					_("Row #{0}: releaser '{1}' has no write access to TS Production "
					  "Entry (needs a Manufacturing or Stores role) — they will be "
					  "blocked at release time until a role is added.").format(
						self.idx, esc(self.releaser_user)),
					indicator="orange")
		elif self.get("releaser_role"):
			# Automatic roles are held by EVERY user but have zero rows in
			# tabHas Role — the holder count below would print the reassuring
			# "no enabled holder" warning while the gate actually admits the whole
			# plant (code-tester FIND-1). Hard-block them.
			try:
				from frappe.permissions import AUTOMATIC_ROLES
			except ImportError:
				AUTOMATIC_ROLES = ("All", "Guest", "Administrator", "Desk User")
			if self.releaser_role in AUTOMATIC_ROLES:
				frappe.throw(
					_("Row #{0}: '{1}' is an automatic role held by every user — it "
					  "would grant release authority to the whole plant. Pick a real "
					  "assigned role (or name a specific user).").format(
						self.idx, esc(self.releaser_role)),
					title=_("Automatic Role Not Allowed"))
			holders = [r[0] for r in frappe.db.sql("""
				SELECT u.name FROM `tabHas Role` hr
				JOIN `tabUser` u ON u.name = hr.parent
				WHERE hr.role = %s AND hr.parenttype = 'User' AND u.enabled = 1
			""", self.releaser_role)]
			if not holders:
				# Warning, not throw (L170 spirit): role churn must never block an
				# unrelated Settings save. Zero holders = admin-only gate meanwhile.
				frappe.msgprint(
					_("Row #{0}: no ENABLED user holds role '{1}' — until someone "
					  "does, only an administrator can release this BOM's runs.").format(
						self.idx, esc(self.releaser_role)),
					indicator="orange")
			else:
				# Same write-access screen as the user branch, across every holder —
				# a holder without TS Production Entry write passes the identity
				# gate but hits a runtime PermissionError at release (live-data F-1).
				no_write = [u for u in holders
				            if not frappe.has_permission("TS Production Entry",
				                                         "write", user=u)]
				if no_write:
					shown = ", ".join(esc(u) for u in no_write[:5])
					more = len(no_write) - 5
					frappe.msgprint(
						_("Row #{0}: {1} of {2} enabled '{3}' holder(s) have no write "
						  "access to TS Production Entry and will be blocked at "
						  "release time: {4}{5}.").format(
							self.idx, len(no_write), len(holders),
							esc(self.releaser_role), shown,
							_(" (+{0} more)").format(more) if more > 0 else ""),
						indicator="orange")

		if self.cost_center:
			cc = frappe.db.get_value(
				"Cost Center", self.cost_center, ["is_group", "company"], as_dict=True)
			if cc and cint(cc.is_group):
				frappe.throw(
					_("Row #{0}: '{1}' is a GROUP cost center — pick a leaf cost center "
					  "entries can book to.").format(self.idx, esc(self.cost_center)),
					title=_("Group Cost Center Not Allowed"))
			if cc and cc.company and bom_company and cc.company != bom_company:
				frappe.throw(
					_("Row #{0}: cost center '{1}' belongs to company '{2}', but BOM {3} "
					  "belongs to '{4}'.").format(
						self.idx, esc(self.cost_center), esc(cc.company), esc(self.bom), esc(bom_company or "")),
					title=_("Cost Center Company Mismatch"))
