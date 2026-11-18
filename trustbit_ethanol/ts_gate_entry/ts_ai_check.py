"""AI Checker: what happens to a TS AI Check Submission.

The form controller is thin on purpose: every rule lives here, so the form, the background job and the
tests share one copy. After Submit the document is never saved again. The job runs as the submitter, who
has no write right on a submitted document, so each later change is a direct database write made under a
row lock.

The job has three steps: read the file into lines (`ts_ai_check_reader`), check each line against the
ERP (`ts_ai_check_match` and `ts_ai_check_rules`), then the AI's review and the bell to the MD
(`ts_ai_check_review`). The report is the MD's. The submitter sees the status and, on their own list,
the points they can fix themselves (`own_points`)."""

import hashlib
import math
import os
from datetime import timedelta

import frappe
from frappe import _
from frappe.utils import background_jobs, cint, cstr, formatdate, get_datetime, now_datetime

from trustbit_ethanol.ts_gate_entry import ts_ai_check_issues as issues
from trustbit_ethanol.ts_gate_entry import ts_ai_check_match as match
from trustbit_ethanol.ts_gate_entry import ts_ai_check_months as months
from trustbit_ethanol.ts_gate_entry import ts_ai_check_mst as mst
from trustbit_ethanol.ts_gate_entry import ts_ai_check_reader as reader
from trustbit_ethanol.ts_gate_entry import ts_ai_check_review as review
from trustbit_ethanol.ts_gate_entry import ts_ai_check_rules as rules

SUBMISSION = "TS AI Check Submission"
REPORT = "TS AI Check Report"
SETTINGS = "TS AI Check Settings"
USAGE = "TS AI Check Usage"
DEFAULT_MODEL = "claude-fable-5-2"
# The two kinds of file. A payment list names suppliers and what each is to be paid, and its check reads
# purchase records: it is sent by Accounts only. An MST report (the month's cost sheets) is also sent by Stores.
PAYMENT, MST = "Payment List", "MST Report"
PAYMENT_ROLES = ("Accounts User", "Accounts Manager")
ST_DRAFT, ST_CHECKING, ST_SENT, ST_UNREADABLE, ST_FAILED = (
	"Draft", "Being checked", "Report sent", "Could not be read", "Check failed")
CONTROL_FIELDS = ("status", "status_note", "submitted_at", "started_at", "finished_at", "lines_read", "file_hash")
NUMBER_FIELDS = ("lines_read",)
# what the AI used for a check; kept in the usage record, which only the MD role reads, never on the submission
USAGE_FIELDS = ("model_used", "input_tokens", "output_tokens", "cost_usd")
JOB_TIMEOUT = 1450  # seconds; the long queue allows 1500
# seconds to wait for the one reader slot; 180 + 60 + the AI's 900 to read + its 240 to review < JOB_TIMEOUT
SUBMIT_WAIT, JOB_WAIT = 5, 180
READS_PER_HOUR = 20  # files one person may send for reading in an hour, refused ones included
STALE_RUNNING_MIN = 30  # longer than the job can run
STALE_QUEUED_MIN = 15
SERVER_FLAG = "ai_check_server_write"
LINE_FIELDS = ("line_no", "page", "source_row", "kind", "supplier_text", "bill_no_text", "bill_date_text",
	"po_text", "amount_text", "amount", "remarks_text", "unclear", "read_note", "section_text", "details_text")
CHECK_FIELDS = ("verdict", "supplier", "supplier_title", "match_how", "list_says", "erp_says", "chain", "receipts_used", "claims")
SUM_FIELDS = ("summary", "coverage", "n_ok", "n_check", "n_serious", "amt_ok", "amt_check", "amt_serious", "figures")
FINDING_FIELDS = ("line_no", "level", "source", "rule", "title", "detail", "refs")
# What the submitter sees of their own checked list, on the form: the points they can fix themselves (a name
# the ERP does not know, a repeated line, an amount or a total that does not fit, a bill the ERP shows as
# paid). Every other rule (approval, gate, stock, on-account, many orders to one supplier, records outside
# the submitter's access), every Note and the AI's review stay in the report.
OWN_RULES = ("S1", "S2", "S3", "S4", "R1", "R2", "R3", "R4", "R5", "U1", "Y1", "Y2", "Y3", "Y4", "M1", "T1", "T3",
	# an MST report's own arithmetic: all of it is in the file the uploader made
	"F1", "F2", "F3", "F4", "F5", "F6", "F7", "F8", "F9",
	# an MST report against the ERP's issues, read in the uploader's own name: theirs to put right or to explain
	"E1", "E2", "E3", "E4", "E5", "E7")
# R2, R4, R5 and the last sentence of Y3 are worked out from every earlier checked list of the company,
# other people's included (line numbers, what those lines asked, what stood behind them, totals over all
# of them). The form never gives those stored words: it gives these, with only the submitter's own
# receipt or order in them. The date in R2's title is kept when the earlier list is the submitter's own.
OWN_WORDS = {
	"R2": "The same amount for this supplier was on an earlier checked list. If that one was not paid, this is fine.",
	"R4": "{0} stood behind a line of an earlier checked list. If that one was paid, part of this line is paid twice.",
	"R5": "More is asked on {0} than its value, on this list or together with earlier checked lists. "
		"If the earlier ones were paid, this pays the same goods again.",
}
OWN_R2_TITLE = "Asked before, on an earlier checked list"


def limits():
	"""The settings with their defaults filled in. A raw read, because the submitter has no read right
	on the Settings document; a blank or missing switch counts as off."""
	raw = frappe.db.get_singles_dict(SETTINGS)
	per_day, file_mb, pages = (cint(raw.get(f)) for f in ("max_checks_per_day", "max_file_mb", "max_pages"))
	return frappe._dict(
		enabled=bool(cint(raw.get("enabled"))),
		model=cstr(raw.get("model")).strip() or DEFAULT_MODEL,
		max_checks_per_day=per_day if per_day > 0 else 20,
		max_file_mb=min(file_mb, 20) if file_mb > 0 else 10,
		max_pages=min(pages, 100) if pages > 0 else 20,
	)


def job_id(name):
	"""One queue id per submission: a check is never queued twice, and the sweep can ask the queue for it."""
	return "ts-ai-check-" + name


def reset_control_fields(doc):
	"""Nothing a browser or a REST call puts in these fields survives a Save or a Submit; only
	`before_submit` (which runs after this) and the job write them."""
	for fieldname in CONTROL_FIELDS:
		doc.set(fieldname, 0 if fieldname in NUMBER_FIELDS else None)
	doc.status = ST_DRAFT


def _own_file_name(doc):
	filters = {"file_url": cstr(doc.get("file")), "owner": doc.owner, "is_private": 1}
	return frappe.db.get_value("File", filters, "name", order_by="creation desc")


def _file_gone():
	frappe.throw(_("The file is no longer attached. Attach it and submit again."))


def check_file_field(doc):
	"""The file must be the submitter's own private upload. Frappe core links ANY existing private URL
	written into an Attach field to the document on save (`attach_files_to_document`), so a File row
	attached to this document proves nothing; the submitter's own upload does, and `validate` runs
	before core makes that link."""
	url = cstr(doc.get("file"))
	if not url:
		return  # the mandatory check reports it
	if not url.startswith("/private/files/"):
		frappe.throw(_("Attach the file as Private."))
	if not _own_file_name(doc):
		frappe.throw(_("Attach the file with the Attach button on this form."))


def own_file(doc):
	"""The submitter's own File row for the attached URL, the newest when the file was uploaded twice.
	The job asks again, because the row can be deleted after Submit."""
	name = _own_file_name(doc)
	if not name:
		_file_gone()
	return frappe.get_doc("File", name)


def file_bytes(doc):
	"""The file as bytes, read from disk: `File.get_content()` returns text for a text file, and the
	hash and the readers need bytes. The size is checked first, so an oversized file is never loaded."""
	path = own_file(doc).get_full_path()
	if not os.path.isfile(path):
		_file_gone()
	max_mb = limits().max_file_mb
	if os.path.getsize(path) > max_mb * 1024 * 1024:
		frappe.throw(_("The file is larger than {0} MB. Send a smaller file.").format(max_mb))
	with open(path, "rb") as f:
		return f.read()


def setup_problem():
	"""Why no file can be read on this site, or "". AI Staff is absent on some sites, so it is imported
	only here and in `ai_call`. The key is only tested for being there; it is never shown or logged."""
	if "trustbit_ai_staff" in frappe.get_installed_apps():
		from trustbit_ai_staff import brain, guards, settings

		if guards.app_enabled() and settings.key() and hasattr(brain, "call_once"):
			return ""
	return _("The checker is not set up on this site. Please tell IT.")


def check_type_allowed(doc):
	"""Who may send which kind of file. The form's rights let Stores make a submission; this keeps theirs to
	the MST report."""
	if doc.check_type != MST and not set(PAYMENT_ROLES) & set(frappe.get_roles()):
		frappe.throw(_("A payment list is sent by Accounts. Choose 'MST Report' as the check type."))


def before_submit(doc):
	"""Everything that can refuse a Submit, so that no list waits for a check that cannot run. The
	sweep comes first: a dead check must not block sending the same file again."""
	if doc.is_new():  # submitted while being created: the naming row would stay locked through the read
		frappe.throw(_("Save the list first, then submit it."))
	if doc.owner != frappe.session.user:  # the check reads the ERP as whoever submits, and its points go to the owner
		frappe.throw(_("Only the person who made this list can submit it."))
	sweep_stale()
	lim = limits()
	if not lim.enabled:
		frappe.throw(_("The checker is switched off. Please tell IT."))
	problem = setup_problem()
	if problem:
		frappe.throw(problem)
	# drafts do not count, and the day starts on the site's clock (the database's own clock is UTC). Each kind
	# of file has the day's limit to itself: MST sheets sent one by one must not use up the payment lists'
	sent_today = {"docstatus": 1, "check_type": doc.check_type, "submitted_at": (">=", now_datetime().strftime("%Y-%m-%d 00:00:00"))}
	if frappe.db.count(SUBMISSION, sent_today) >= lim.max_checks_per_day:
		frappe.throw(
			_("Today's limit of {0} checks is reached. Try again tomorrow.").format(lim.max_checks_per_day))
	data = file_bytes(doc)
	file_hash = hashlib.sha256(data).hexdigest()
	if frappe.db.exists(SUBMISSION, {"file_hash": file_hash, "docstatus": 1, "status": ST_CHECKING}):
		frappe.throw(_("This file is already being checked. Wait for that check to finish."))
	# the server reads one file at a time, and a refused Submit is not in the daily count: a limit per person
	reads = frappe.cache.make_key("ts_ai_check_reads", user=frappe.session.user)
	frappe.cache.set(reads, 0, ex=3600, nx=True)  # the hour starts at the first read
	if frappe.cache.incr(reads) > READS_PER_HOUR:
		frappe.throw(_("You have sent many files in the last hour. Please try again later."))
	try:
		# in its own process, with a memory and a time limit
		reader.prepare(data, lim, wait=SUBMIT_WAIT, mst=doc.check_type == MST)
	except reader.Busy:
		frappe.throw(_("The checker is busy with another file. Please submit again in a minute."))
	except reader.Unreadable as e:
		frappe.throw(str(e))
	except reader.ServiceError as e:
		_log(doc.name, e.detail)
		frappe.throw(_("The file could not be checked. Please tell IT."))
	doc.file_hash = file_hash
	doc.submitted_at = now_datetime()


def start(doc):
	"""Queue the check. The status is a direct write because the document is already saved, and the
	job is queued only after the commit, so the worker finds the submitted row."""
	doc.db_set("status", ST_CHECKING, update_modified=False)
	frappe.enqueue("trustbit_ethanol.ts_gate_entry.ts_ai_check.run_check", queue="long", timeout=JOB_TIMEOUT,
		job_id=job_id(doc.name), deduplicate=True, enqueue_after_commit=True, submission=doc.name)


def on_form_load(doc):
	"""Close a dead check when its form is opened, and hand the form what it may not read itself (the
	submitter cannot read Settings). A form load is a GET, which Frappe rolls back unless the commit
	flag is set; the write is idempotent and only closes a check that is already dead."""
	if doc.docstatus == 1 and doc.status == ST_CHECKING and sweep_stale(doc.name):
		frappe.local.flags.commit = True
		doc.reload()
	doc.set_onload("eta_min", eta_minutes(doc.check_type))
	points = own_points(doc)
	if points is not None:
		doc.set_onload("own_points", points)


def own_points(doc):
	"""For the form of a checked list, and only for the person who submitted it: the points of `OWN_RULES`
	graded Serious or To check, each with its line as the file gave it. `None` means there is nothing to
	show (not checked, or the reader is not the submitter); an empty list means no such point was found.
	The counts of the report are not given: they would tell how many other points the MD has.
	The check's own last write is in the submitter's name. A list that another account submitted (possible
	before Submit was limited to the owner) was read with that account's access, so it gives no points."""
	if doc.docstatus != 1 or doc.status != ST_SENT or doc.owner != frappe.session.user or doc.modified_by != doc.owner:
		return None
	return _own_points(doc.name, doc.owner)


def _own_points(name, owner):
	if not frappe.db.exists(REPORT, name):  # the report has the submission's name
		return None
	found = frappe.get_all("TS AI Check Finding", filters={"parent": name, "parenttype": REPORT, "source": "Rule",
		"rule": ("in", (*OWN_RULES, "X1")), "level": ("in", (rules.SERIOUS, rules.CHECK))},
		fields=["line_no", "level", "rule", "title", "detail", "refs"], order_by="line_no asc, idx asc", limit_page_length=0)
	lines = {row.line_no: row for row in frappe.get_all("TS AI Check Line", filters={"parent": name, "parenttype": REPORT},
		fields=["line_no", "supplier_text", "amount", "match_how"], limit_page_length=0)} if found else {}
	points = []
	for f in found:
		line = lines.get(f.line_no) or {}
		words = own_words(f.rule, f.title, f.detail, f.refs, line.get("match_how"), owner)
		if words:
			points.append({"line_no": cint(f.line_no), "level": f.level, "title": words[0], "detail": words[1],
				"supplier": line.get("supplier_text"), "amount": rules.money(line.get("amount")) if line else ""})
	return points


def own_words(rule, title, detail, refs, match_how, owner):
	"""A point's (title, detail) as the submitter reads them, or None when the point is not for them."""
	refs = [ref.strip() for ref in cstr(refs).split(",") if ref.strip()]
	if rule == "X1":
		# Records the submitter cannot read stand behind this supplier. That is the MD's to know: the form
		# says what it would say with no such records (M1, or nothing for an on-account line), so a
		# missing point tells nothing either.
		return (rules.NONE_HOW, rules.NONE_DETAIL) if match_how == rules.NONE_HOW else None
	if rule == "R2":  # the first reference is the earlier report
		own = bool(refs) and frappe.db.get_value(REPORT, refs[0], "submitted_by") == owner
		return title if own else OWN_R2_TITLE, OWN_WORDS["R2"]
	if rule == "R4":  # the earlier report, then the receipts of this line that it shares
		return title, OWN_WORDS["R4"].format(refs[1] if len(refs) > 1 else "A receipt")
	if rule == "R5":  # the order of this line
		return title, OWN_WORDS["R5"].format(refs[0] if refs else "this order")
	if rule == "Y3":  # what the ERP shows stays; the sum asked on the order's receipts over all lists does not
		return title, cstr(detail).split(" With this line")[0]
	return title, detail


def own_bell(doc):
	"""One bell to the submitter when their list is checked, next to the MD's. The subject is the list date
	and a count only: no word from the file goes into it. A submitter the MD's bell already reaches gets no
	second one, and a check that ran in another account's name rings nobody. A bell that fails never stops
	the report; IT gets the trace."""
	try:
		from trustbit_ethanol.ts_gate_entry.ts_notification_routing import role_recipients
		from trustbit_ethanol.ts_gate_entry.ts_production_notify import _notification_log

		if doc.owner != frappe.session.user or doc.owner in role_recipients("MD"):
			return 0
		count = len(_own_points(doc.name, doc.owner) or [])
		subject = (_("Your MST report of {0} is checked.").format(month(doc.list_date) or doc.name) if doc.check_type == MST
			else _("Your payment list of {0} is checked.").format(rules.day(doc.list_date) or doc.name)) + " " + (
			_("{0} point(s) for you to look at.").format(count) if count else _("No point for you to correct."))
		_notification_log(doc.owner, subject, subject, SUBMISSION, doc.name)
		return 1
	except Exception:
		_log(cstr(getattr(doc, "name", "")), frappe.get_traceback())
		frappe.clear_messages()
		return 0


def month(value):
	"""The month an MST report is for, as words: 'September 2026'."""
	return formatdate(value, "MMMM yyyy") if value else ""


def eta_minutes(kind=PAYMENT) -> int | None:
	"""How long the last ten finished checks of this kind took on average, so the form can say when to look
	again. An MST report is checked in seconds and a payment list in minutes: each has its own figure."""
	rows = frappe.get_all(SUBMISSION, filters={"docstatus": 1, "status": ST_SENT, "check_type": kind},
		fields=["submitted_at", "finished_at"], order_by="finished_at desc", limit=10)
	seconds = [(get_datetime(r.finished_at) - get_datetime(r.submitted_at)).total_seconds()
		for r in rows if r.submitted_at and r.finished_at]
	if not seconds:
		return None
	return max(1, math.ceil(sum(seconds) / len(seconds) / 60))


def _is_stale(row):
	now = now_datetime()
	if row.started_at:
		return get_datetime(row.started_at) < now - timedelta(minutes=STALE_RUNNING_MIN)
	if not row.submitted_at or get_datetime(row.submitted_at) >= now - timedelta(minutes=STALE_QUEUED_MIN):
		return False
	try:
		return not background_jobs.is_job_enqueued(job_id(row.name))
	except Exception:
		return False  # the queue cannot be asked: close a check only when it is known to be dead


def sweep_stale(name=None) -> int:
	"""A worker that dies, or a queue that never got the job, would leave a list at "Being checked"
	for ever. This closes such checks, so the submitter is told to send the file again. Each row is
	locked and tested again before the write, because the job may be finishing at this moment."""
	filters = {"docstatus": 1, "status": ST_CHECKING}
	if name:
		filters["name"] = name
	fields = ["name", "docstatus", "status", "started_at", "submitted_at"]
	marked = 0
	for row in frappe.get_all(SUBMISSION, filters=filters, fields=fields):
		if not _is_stale(row):
			continue
		fresh = frappe.db.get_value(SUBMISSION, row.name, fields, as_dict=True, for_update=True)
		if fresh and fresh.docstatus == 1 and fresh.status == ST_CHECKING and _is_stale(fresh):
			note = _("The check did not finish. Please submit the file again.")
			values = {"status": ST_FAILED, "status_note": note, "finished_at": now_datetime()}
			# whoever set the sweep off did not edit this document: its last-edited stamp stays
			frappe.db.set_value(SUBMISSION, row.name, values, update_modified=False)
			marked += 1
	return marked


def run_check(submission):
	"""The background job, run as the submitter: read the file into lines, check them against the ERP,
	add the AI's review, store the report and ring the MD's bell.
	The start is stamped and committed before any work, so a second worker returns at the guard and a
	crash leaves a start time for the sweep. A failure rolls the half-made report back first."""
	row = frappe.db.get_value(SUBMISSION, submission, ["docstatus", "status", "started_at"],
		as_dict=True, for_update=True)
	if not row or row.docstatus != 1 or row.status != ST_CHECKING or row.started_at:
		return
	frappe.db.set_value(SUBMISSION, submission, "started_at", now_datetime(), update_modified=False)
	_commit()
	spent = {}  # what the AI calls used so far: a check that fails after a paid read still records it
	try:
		doc = frappe.get_doc(SUBMISSION, submission)
		lim = limits()
		try:
			data = file_bytes(doc)
		except frappe.ValidationError:
			data = None
		if data is None or hashlib.sha256(data).hexdigest() != doc.file_hash:
			frappe.clear_messages()  # not the bytes that were checked at Submit: deleted or changed since
			raise reader.Unreadable(_("The file is no longer attached. Attach it and submit again."))
		try:  # every local read, in its own process with limits
			info = reader.prepare(data, lim, full=True, wait=JOB_WAIT, mst=doc.check_type == MST)
		except (reader.Busy, reader.ServiceError) as e:  # our side, not the AI service's
			raise RuntimeError(getattr(e, "detail", "the reader stayed busy")) from e
		if doc.check_type == MST:  # code: the workbook's own arithmetic, its lines against the month's ERP issues, last month's report
			result, checked = mst.report(info.book, info.kind, erp=issues.fetch(doc.company, doc.list_date),
				month={"day": doc.list_date, "before": months.before(doc.company, doc.list_date)})
			reviewed = review.run(doc, result, checked, ai_call)  # never raises: the report does not wait on the AI
		else:
			result = info.table or reader.read_document(data, info, lim, ai_call)
			spent = _spent(result.meta, {})
			checked = match.check(doc, result)  # code only: a failure here fails the check, it never sends half a report
			reviewed = review.run(doc, result, checked, ai_call)  # never raises: the report does not wait on the AI
		spent = _spent(result.meta, reviewed.meta)
		_save_report(doc, result, checked, reviewed)
		if _finish(submission, ST_SENT, None, lines_read=len(result.lines), **spent):
			review.bell(submission)  # the report has the submission's name
			own_bell(doc)
		else:
			_rollback()  # a sweep closed it first: leave no report behind
	except reader.Unreadable as e:
		_rollback()
		_finish(submission, ST_UNREADABLE, cstr(e), **e.meta)
	except reader.ServiceError as e:
		_rollback()
		_log(submission, e.detail)
		note = _("The AI service could not be reached. Please submit the file again later.")
		_finish(submission, ST_FAILED, note, **e.meta)
	except Exception:
		_rollback()
		_log(submission, frappe.get_traceback())
		_finish(submission, ST_FAILED, _("Something went wrong on our side. Please submit the file again."), **spent)
	_commit()


def _finish(name, status, note, **vals) -> bool:
	"""Close the check, unless something else already did: the lock makes the test and the write one
	step. `notify` makes an open form show the result by itself."""
	if frappe.db.get_value(SUBMISSION, name, "status", for_update=True) != ST_CHECKING:
		return False
	usage = {fieldname: vals.pop(fieldname) for fieldname in USAGE_FIELDS if fieldname in vals}
	values = {**vals, "status": status, "status_note": note, "finished_at": now_datetime()}
	frappe.get_doc(SUBMISSION, name).db_set(values, notify=True)
	try:
		save_usage(name, usage)
	except Exception:  # the cost record must never stop the status change
		_log(name, frappe.get_traceback())
	return True


def save_usage(name, usage):
	"""The AI model, tokens and cost of one check, in their own record. The submitter runs the job and
	has no right on that record, so the write is the server's."""
	usage = {fieldname: usage[fieldname] for fieldname in USAGE_FIELDS if usage.get(fieldname)}
	if not usage:
		return
	if frappe.db.exists(USAGE, name):
		frappe.db.set_value(USAGE, name, usage, update_modified=False)
		return
	doc = frappe.get_doc({"doctype": USAGE, "submission": name, **usage})
	doc.flags[SERVER_FLAG] = True
	doc.insert(ignore_permissions=True)


def usage_value(name, fieldname):
	"""One AI detail of a check for the form, and only for someone who may read the usage record."""
	if not name or fieldname not in USAGE_FIELDS or not frappe.has_permission(USAGE, "read"):
		return None
	return frappe.db.get_value(USAGE, name, fieldname)


def _spent(read, reviewed):
	"""What the two AI calls used, added up. A table read by code uses none."""
	read, reviewed = read or {}, reviewed or {}
	if not read and not reviewed:
		return {}
	out = {"model_used": read.get("model_used") or reviewed.get("model_used") or limits().model}
	for fieldname in ("input_tokens", "output_tokens", "cost_usd"):
		out[fieldname] = (read.get(fieldname) or 0) + (reviewed.get(fieldname) or 0)
	if not any(out[fieldname] for fieldname in ("input_tokens", "output_tokens", "cost_usd")):
		return {}  # a call that failed with nothing used: no usage to record
	return out


def _save_report(doc, result, checked, reviewed):
	"""One report per submission, made only here: the flag is what the report's controller lets
	through, and the submitter has no right of their own on the report."""
	report = frappe.new_doc(REPORT)
	report.update({
		"submission": doc.name, "check_type": doc.check_type, "company": doc.company,
		"list_date": doc.list_date, "submitted_by": doc.owner, "submitted_on": doc.submitted_at,
		"file_kind": result.file_kind, "read_by": result.read_by,
		"pages_in_file": result.pages_in_file, "pages_seen": result.pages_seen,
		"lines_total": len(result.lines), "list_total": result.list_total,
		"printed_total": result.printed_total, "read_notes": "\n".join(result.notes),
		"ai_summary": reviewed.summary, "ai_note": reviewed.note,
		**{fieldname: checked.get(fieldname) for fieldname in SUM_FIELDS},
	})
	for line, extra in zip(result.lines, checked.lines):
		row = {fieldname: line.get(fieldname) for fieldname in LINE_FIELDS}
		report.append("lines", {**row, **{fieldname: extra.get(fieldname) for fieldname in CHECK_FIELDS}})
	for finding in [*checked.findings, *reviewed.points]:
		report.append("findings", {fieldname: finding.get(fieldname) for fieldname in FINDING_FIELDS})
	report.flags[SERVER_FLAG] = True
	report.insert(ignore_permissions=True)


def ai_call(system, content, schema, max_tokens=64000, effort="high", deadline_s=900):
	"""The only place that touches AI Staff, so a site without that app can still import this module
	and a test can put a fake in its place."""
	from trustbit_ai_staff import brain

	return brain.call_once(model=limits().model, system=system, content=content, schema=schema,
		max_tokens=max_tokens, effort=effort, deadline_s=deadline_s, lang="English")


def _log(submission, message):
	"""A row in the Error Log for IT, which must never stop the status change. The title never holds text
	from the file or the AI; a blank message would make Frappe log the local variables (the file)."""
	try:
		frappe.log_error(title="AI Checker: " + submission, message=cstr(message) or "No detail was given.")
	except Exception:
		pass


def _commit():
	"""One function so tests can replace it."""
	frappe.db.commit()


def _rollback():
	"""One function so tests can replace it: a real rollback also drops a test's own records."""
	frappe.db.rollback()
