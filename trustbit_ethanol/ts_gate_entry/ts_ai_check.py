"""AI Checker, step 1: what happens to a TS AI Check Submission.

The form controller is thin on purpose: every rule lives here, so the form, the background job and the
tests share one copy. After Submit the document is never saved again. The job runs as the submitter, who
has no write right on a submitted document, so each later change is a direct database write made under a
row lock."""

import hashlib
import math
import os
from datetime import timedelta

import frappe
from frappe import _
from frappe.utils import background_jobs, cint, cstr, get_datetime, now_datetime

from trustbit_ethanol.ts_gate_entry import ts_ai_check_reader as reader

SUBMISSION = "TS AI Check Submission"
REPORT = "TS AI Check Report"
SETTINGS = "TS AI Check Settings"
DEFAULT_MODEL = "claude-fable-5-2"
ST_DRAFT, ST_CHECKING, ST_SENT, ST_UNREADABLE, ST_FAILED = (
	"Draft", "Being checked", "Report sent", "Could not be read", "Check failed")
CONTROL_FIELDS = ("status", "status_note", "submitted_at", "started_at", "finished_at", "lines_read",
	"model_used", "input_tokens", "output_tokens", "cost_usd", "file_hash")
NUMBER_FIELDS = ("lines_read", "input_tokens", "output_tokens", "cost_usd")
JOB_TIMEOUT = 1200  # seconds; the long queue allows 1500
SUBMIT_WAIT, JOB_WAIT = 5, 180  # seconds to wait for the one reader slot; 180 + 60 + the AI's 900 < JOB_TIMEOUT
READS_PER_HOUR = 20  # files one person may send for reading in an hour, refused ones included
STALE_RUNNING_MIN = 30  # longer than the job can run
STALE_QUEUED_MIN = 15
SERVER_FLAG = "ai_check_server_write"
LINE_FIELDS = ("line_no", "page", "source_row", "kind", "supplier_text", "bill_no_text", "bill_date_text",
	"po_text", "amount_text", "amount", "remarks_text", "unclear", "read_note")


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


def before_submit(doc):
	"""Everything that can refuse a Submit, so that no list waits for a check that cannot run. The
	sweep comes first: a dead check must not block sending the same file again."""
	if doc.is_new():  # submitted while being created: the naming row would stay locked through the read
		frappe.throw(_("Save the list first, then submit it."))
	sweep_stale()
	lim = limits()
	if not lim.enabled:
		frappe.throw(_("The checker is switched off. Please tell IT."))
	problem = setup_problem()
	if problem:
		frappe.throw(problem)
	# drafts do not count, and the day starts on the site's clock (the database's own clock is UTC)
	sent_today = {"docstatus": 1, "submitted_at": (">=", now_datetime().strftime("%Y-%m-%d 00:00:00"))}
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
		reader.prepare(data, lim, wait=SUBMIT_WAIT)  # in its own process, with a memory and a time limit
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
	doc.set_onload("eta_min", eta_minutes())


def eta_minutes() -> int | None:
	"""How long the last ten finished checks took on average, so the form can say when to look again."""
	rows = frappe.get_all(SUBMISSION, filters={"docstatus": 1, "status": ST_SENT},
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
	"""The background job, run as the submitter: read the file into lines and store them on the report.
	The start is stamped and committed before any work, so a second worker returns at the guard and a
	crash leaves a start time for the sweep. A failure rolls the half-made report back first."""
	row = frappe.db.get_value(SUBMISSION, submission, ["docstatus", "status", "started_at"],
		as_dict=True, for_update=True)
	if not row or row.docstatus != 1 or row.status != ST_CHECKING or row.started_at:
		return
	frappe.db.set_value(SUBMISSION, submission, "started_at", now_datetime(), update_modified=False)
	_commit()
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
			info = reader.prepare(data, lim, full=True, wait=JOB_WAIT)
		except (reader.Busy, reader.ServiceError) as e:  # our side, not the AI service's
			raise RuntimeError(getattr(e, "detail", "the reader stayed busy")) from e
		result = info.table or reader.read_document(data, info, lim, ai_call)
		_save_report(doc, result)
		if not _finish(submission, ST_SENT, None, lines_read=len(result.lines), **(result.meta or {})):
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
		_finish(submission, ST_FAILED, _("Something went wrong on our side. Please submit the file again."))
	_commit()


def _finish(name, status, note, **vals) -> bool:
	"""Close the check, unless something else already did: the lock makes the test and the write one
	step. `notify` makes an open form show the result by itself."""
	if frappe.db.get_value(SUBMISSION, name, "status", for_update=True) != ST_CHECKING:
		return False
	values = {**vals, "status": status, "status_note": note, "finished_at": now_datetime()}
	frappe.get_doc(SUBMISSION, name).db_set(values, notify=True)
	return True


def _save_report(doc, result):
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
	})
	for line in result.lines:
		report.append("lines", {fieldname: line.get(fieldname) for fieldname in LINE_FIELDS})
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
