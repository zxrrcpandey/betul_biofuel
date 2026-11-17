"""Makes Frappe's dashboard-chart data call answer only for what the caller may read.

`frappe.desk.doctype.dashboard_chart.dashboard_chart.get` is open to every logged-in user and takes the
chart as JSON from the caller, so the caller picks the doctype, the column to add up and the filters.
Two things in it leave permission out:
- a Heatmap chart reads with `frappe.get_all`, which checks nothing: anyone can sum or count any column
  of any doctype per day (Frappe's develop branch reads with `get_list`; version-15 does not yet);
- results are kept in one shared cache per chart name, so what was worked out for one user is handed
  to the next, whatever that user may read.
Here the Heatmap reads with `frappe.get_list` and the call skips the shared cache, so every answer is
worked out with the caller's own rights on the chart's doctype: role, user permissions, our
confidentiality rules. It is exactly as strict as `frappe.get_list`, no stricter: rows of a child table
are checked against the parent doctype's role only, and a restricted field is not refused when it sits
inside an expression with `*` (both measured on demo, 17 Nov 2026; both are the same in a plain list call).
Applied from rename_guard's before_request hook. Delete after a Frappe upgrade that has both.

Also here: ERPNext's account-balance chart source (`account_balance_timeline.get`) reads GL Entry with no
permission check, so any logged-in user could read any ledger account's balance over time (measured on
demo with a gate-role user). It now answers only users who may read Account and GL Entry.

And ERPNext's `get_balance_on`: it takes `ignore_account_permission` from the caller and checks nothing
when no account is named, so any login could read ledger balances (measured on demo). ERPNext's own
function stays for server code but is taken off the web whitelist, which closes every module name that
imports it; the web gets `balance_on` (hooks.py override_whitelisted_methods): ledger readers only.
NOT closed here: other ERPNext calls that pass the caller's party or account on to it, e.g.
payment_entry.get_party_and_account_balance / get_party_details (party balances, no check).
"""

import inspect

import frappe
from frappe.desk.doctype.dashboard_chart import dashboard_chart as core
from frappe.utils import cint, getdate, nowdate

try:
	from erpnext.accounts.dashboard_chart_source.account_balance_timeline import account_balance_timeline as ledger
except ImportError:  # the source moved or went: nothing to guard, and the site must keep answering
	ledger = None
try:
	from erpnext.accounts import utils as accounts
except ImportError:
	accounts = None

# Frappe's own function, without its shared-cache wrapper. Kept on Frappe's module, so a second import of
# this file (after `apply`) still finds Frappe's function and not the guard itself.
_core_get = getattr(core, "_ts_core_get", None) or inspect.unwrap(core.get)
core._ts_core_get = _core_get


@frappe.whitelist()
def get(chart_name=None, chart=None, no_cache=None, filters=None, from_date=None, to_date=None,
		timespan=None, time_interval=None, heatmap_year=None, refresh=None):
	return _core_get(chart_name=chart_name, chart=chart, no_cache=no_cache, filters=filters, from_date=from_date,
		to_date=to_date, timespan=timespan, time_interval=time_interval, heatmap_year=heatmap_year, refresh=refresh)


def heatmap(chart, filters, heatmap_year):
	"""Frappe's `get_heatmap_chart_config`, with `get_list` in place of `get_all`."""
	doctype, datefield = chart.document_type, chart.based_on
	year = cint(heatmap_year) if heatmap_year else getdate(nowdate()).year
	filters.append([doctype, datefield, ">", f"{year}-01-01", False])
	filters.append([doctype, datefield, "<", f"{year + 1}-01-01", False])
	stamp = f"unix_timestamp({datefield})" if frappe.db.db_type == "mariadb" else f"extract(epoch from timestamp {datefield})"
	value = f"{core.get_aggregate_function(chart.chart_type)}({chart.value_based_on or '1'})"
	data = frappe.get_list(doctype, fields=[stamp, value], filters=filters, group_by=f"date({datefield})",
		as_list=1, order_by=f"{datefield} asc", ignore_ifnull=True, parent_doctype=chart.parent_document_type)
	return {"labels": [], "dataPoints": dict(data)}


if ledger:
	_ledger_get = getattr(ledger, "_ts_core_get", None) or inspect.unwrap(ledger.get)
	ledger._ts_core_get = _ledger_get


@frappe.whitelist()
def ledger_get(chart_name=None, chart=None, no_cache=None, filters=None, from_date=None, to_date=None,
		timespan=None, time_interval=None, heatmap_year=None):
	for doctype in ("Account", "GL Entry"):
		frappe.has_permission(doctype, "read", throw=True)
	return _ledger_get(chart_name=chart_name, chart=chart, no_cache=no_cache, filters=filters, from_date=from_date,
		to_date=to_date, timespan=timespan, time_interval=time_interval, heatmap_year=heatmap_year)


@frappe.whitelist()
def balance_on(account=None, date=None, party_type=None, party=None, company=None, in_account_currency=True,
		cost_center=None, account_type=None, start_date=None):
	"""The web version of ERPNext's `get_balance_on`: no `ignore_account_permission` from the caller."""
	frappe.has_permission("GL Entry", "read", throw=True)
	return accounts.get_balance_on(account=account, date=date, party_type=party_type, party=party, company=company,
		in_account_currency=in_account_currency, cost_center=cost_center, account_type=account_type, start_date=start_date)


_balance_off_web = False


def apply():
	global _balance_off_web
	if accounts and not _balance_off_web:
		if accounts.get_balance_on in frappe.whitelisted:
			frappe.whitelisted.remove(accounts.get_balance_on)
		_balance_off_web = True
	if core.get is not get:
		core.get = get
		core.get_heatmap_chart_config = heatmap
	if ledger and ledger.get is not ledger_get:
		ledger.get = ledger_get
