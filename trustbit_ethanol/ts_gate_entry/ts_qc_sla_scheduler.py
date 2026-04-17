"""
v2.8.1 Phase B — QC SLA monitoring.

Two scheduler hooks:
  - scan_overdue_qc_30min: every 30 min, counts PRs stuck >72h at ts_qc_status='Pending'
  - weekly_qc_email: Monday 09:00 IST, emails MD + CEO + IT Head with stuck PR summary

Feature flag: TS Settings.ts_qc_gate_enabled. SLA tracks regardless but email only
fires when gate is enabled (otherwise PR.ts_qc_status is advisory).

Refs: memory/project_flow_revamp_v2.8.md (Q3 decision)
"""

import frappe
from frappe.utils import now_datetime


def _gate_is_enabled():
	try:
		return bool(frappe.db.get_single_value("TS Settings", "ts_qc_gate_enabled"))
	except Exception:
		return False


def scan_overdue_qc_30min():
	"""Cron every 30 min — query PRs stuck at Pending QC >72h, cache count."""
	if not _gate_is_enabled():
		return
	try:
		count = frappe.db.sql("""
			SELECT COUNT(*)
			FROM `tabPurchase Receipt`
			WHERE ts_qc_status = 'Pending'
			  AND docstatus = 1
			  AND TIMESTAMPDIFF(HOUR, posting_date, NOW()) > 72
		""")
		if count:
			frappe.cache().set_value("ts_qc_overdue_count", int(count[0][0] or 0), expires_in_sec=3600)
	except Exception as e:
		frappe.log_error(message=f"scan_overdue_qc_30min: {e}", title="ts_qc_sla_scheduler")


def weekly_qc_email():
	"""Cron Monday 09:00 — email MD + CEO + IT Head with overdue PR summary."""
	if not _gate_is_enabled():
		return
	try:
		stuck = frappe.db.sql("""
			SELECT pr.name, pr.supplier, pr.posting_date, pr.ts_token, pr.cost_center,
			       TIMESTAMPDIFF(HOUR, pr.posting_date, NOW()) AS hours_overdue
			FROM `tabPurchase Receipt` pr
			WHERE pr.ts_qc_status = 'Pending'
			  AND pr.docstatus = 1
			  AND TIMESTAMPDIFF(HOUR, pr.posting_date, NOW()) > 72
			ORDER BY pr.cost_center, hours_overdue DESC
		""", as_dict=True)

		if not stuck:
			return  # Nothing to report — skip silent

		# Build recipient list: MD + CEO + IT Head
		recipients = frappe.db.sql_list("""
			SELECT DISTINCT hr.parent
			FROM `tabHas Role` hr
			JOIN `tabUser` u ON u.name = hr.parent
			WHERE hr.role IN ('MD', 'Managing Director', 'CEO', 'IT Head')
			  AND u.enabled = 1
			  AND hr.parenttype = 'User'
		""")

		if not recipients:
			return

		# Build HTML table grouped by CC
		rows_html = []
		for r in stuck:
			rows_html.append(
				f"<tr><td>{frappe.utils.escape_html(r.name or '')}</td>"
				f"<td>{frappe.utils.escape_html(r.ts_token or '')}</td>"
				f"<td>{frappe.utils.escape_html(r.supplier or '')}</td>"
				f"<td>{frappe.utils.escape_html(r.cost_center or '')}</td>"
				f"<td>{r.posting_date}</td>"
				f"<td style='color:#dc2626;font-weight:bold;'>{r.hours_overdue}h</td></tr>"
			)
		message = f"""
			<h3 style="color:#1e40af;">Weekly QC Overdue Report</h3>
			<p>{len(stuck)} Purchase Receipt(s) pending Quality Inspection for &gt;72 hours:</p>
			<table border="1" cellpadding="8" cellspacing="0" style="border-collapse:collapse;width:100%;font-family:Arial;">
				<tr style="background:#f1f5f9;">
					<th>PR</th><th>Token</th><th>Supplier</th><th>Cost Center</th><th>Posted</th><th>Overdue</th>
				</tr>
				{''.join(rows_html)}
			</table>
			<p style="margin-top:16px;color:#475569;">
				Action required: Quality Lab should complete inspections to unblock supplier payment.
				Override available on each PI (CEO / SM / IT Head) with documented reason.
			</p>
			<p style="font-size:11px;color:#94a3b8;">— Trustbit Ethanol (BBF) — QC SLA Monitor</p>
		"""

		frappe.sendmail(
			recipients=recipients,
			subject=f"[BBF] Weekly QC Overdue Report — {len(stuck)} stuck PR(s)",
			message=message,
			now=False,  # Queue via EmailQueue for audit
		)
	except Exception as e:
		frappe.log_error(message=f"weekly_qc_email: {e}", title="ts_qc_sla_scheduler")
