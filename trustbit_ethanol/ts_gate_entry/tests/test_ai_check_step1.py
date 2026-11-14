"""AI Checker, step 1 tests: the file check at Submit, reading a list into lines, the life of a check, and
who may see or change what. Demo only; every write is rolled back at the end.

Nothing leaves the transaction. The job module's commit becomes a savepoint and its rollback goes back to
that savepoint, so "the start stamp survives" and "no report is left" can both be seen. The queue and the
Error Log (a MyISAM table: a rollback would not remove its rows) are recorded instead of written, and the AI
is a stand-in that answers what a test tells it to: no background job is started and no AI is called.

Usage (same shape as test_regression.py):
    cd /home/frappe/frappe-bench/sites && sudo -u frappe ../env/bin/python \
        ../apps/trustbit_ethanol/trustbit_ethanol/ts_gate_entry/tests/test_ai_check_step1.py
Exit code: 0 = all passed, 1 = failures.
"""

import base64
import csv
import hashlib
import io
import itertools
import os
import re
import sys
import zipfile
import zlib
from datetime import timedelta

import frappe

SITE = os.environ.get("FRAPPE_SITE") or "betulbiofuel.mkasystem.com"
RESULTS = []
SUB, REPORT, LINE = "TS AI Check Submission", "TS AI Check Report", "TS AI Check Line"
SETTINGS = "TS AI Check Settings"
REFUSED = (frappe.ValidationError, frappe.PermissionError, frappe.DuplicateEntryError)
DENIED = frappe.PermissionError
MODEL = "claude-fable-5-2"
SP_JOB, SP_REFUSED = "aichk_job", "aichk_refused"
SEQ = itertools.count(1)
PREFIX = "aichk-test-" + os.urandom(4).hex() + "-"  # no file of an earlier run or of a real user has this name
FILES_MADE = []
COMPANY = None
ai = reader = None  # the two modules under test, imported once the site is connected
KEPT = {}  # what the stand-ins replaced in the job module, put back at the end
SWAPPED = ("frappe", "background_jobs", "ai_call", "setup_problem", "_commit", "_rollback", "_finish", "READS_PER_HOUR")

# the build contract's sentences, typed here on their own (never read from the code under test)
M2 = "Attach the file as Private."
M3 = "Attach the file with the Attach button on this form."
M4 = "The file is larger than {0} MB. Send a smaller file."
M4B = "The Excel file is too large inside. Save a smaller copy and attach again."
M5 = "This file type cannot be checked. Use Excel, CSV, PDF or a JPG/PNG photo."
M6 = "The file is empty."
M7 = "The file has a password. Remove it and attach again."
M8 = "The file could not be opened. Save it again and attach."
M9 = "The PDF has {0} pages and the limit is {1}. Split it and submit each part."
M10 = "The photo is too large. Send a smaller photo or a PDF."
M11 = "The list needs a supplier column and an amount column. Add the column headings and attach again."
M12 = "The list has no rows."
M13A = "The list has more than 2,000 rows. Split it and submit each part."
M13B = "The list has more than 60 columns. Remove the unused columns and attach again."
M14 = "This file is already being checked. Wait for that check to finish."
M15 = "The checker is switched off. Please tell IT."
M16 = "The checker is not set up on this site. Please tell IT."
M17 = "Today's limit of {0} checks is reached. Try again tomorrow."
M18 = "The file is no longer attached. Attach it and submit again."
SMALL_PHOTO = "The photo is too small to read. Attach it again with Optimize unticked, or send a PDF."
M21 = "The AI service could not be reached. Please submit the file again later."
M22 = "Something went wrong on our side. Please submit the file again."
M23 = "The check did not finish. Please submit the file again."
M24 = "This file does not look like a payment list."
M25 = "The file could not be read clearly. Scan it again, or send the Excel file."
M26 = "The list is too long to read in one go. Send it as Excel, or split it."
M27 = "The AI service declined to read this file. Send the Excel file instead."
M28 = "The file is too large to read. Save the payment list on its own and attach again."
M29 = "The file could not be checked. Please tell IT."
M30 = "The checker is busy with another file. Please submit again in a minute."
M31 = "You have sent many files in the last hour. Please try again later."
M32 = "Save the list first, then submit it."
NO_CHANGE = "A submitted list cannot be changed. Submit a new one."
NO_CANCEL = "A submitted list cannot be cancelled. Submit a new one."
JOB_ONLY = "Reports are made by the checker only."
NO_REPORT_CHANGE = "A report cannot be changed."
ROW_ALONE = "Change this row from its report."
BAD_MODEL = "The model name is not valid."

S1, S2, S3, S4 = "Sunrise Traders", "Hilltop Engineering", "Riverbend Supplies", "Lakeside Tools"
HEAD = ["Supplier Name", "Bill No", "PO No", "Amount", "Remarks"]
LINE_FIELDS = ("line_no", "page", "source_row", "kind", "supplier_text", "bill_no_text", "bill_date_text",
	"po_text", "amount_text", "amount", "remarks_text", "unclear", "read_note")
# what a forged request sends in the control fields, and what a Draft must hold instead
FORGED = {"status": "Report sent", "status_note": "forged", "submitted_at": "2001-01-01 10:00:00",
	"started_at": "2001-01-01 10:00:00", "finished_at": "2001-01-01 10:00:00", "lines_read": 99,
	"model_used": "forged", "input_tokens": 5, "output_tokens": 5, "cost_usd": 9.5, "file_hash": "f" * 64}
CLEAN = {"status": "Draft", "status_note": None, "submitted_at": None, "started_at": None, "finished_at": None,
	"lines_read": 0, "model_used": None, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0, "file_hash": None}
# what the AI stand-in's usual answer must leave on the submission (cache tokens count as input)
SPENT = {"model_used": MODEL, "input_tokens": 1300, "output_tokens": 300, "cost_usd": 0.0221}
# one invented old-Excel list, made once with xlwt (the servers have no .xls writer); zlib, then base64.
# Sheet "Payments": a title row, headings on row 3 (Party Name, Bill No, Bill Date, PO No, Amount to Pay,
# Remarks), three rows, a Total row; then a hidden sheet "Old" with one decoy row.
XLS = zlib.decompress(base64.b64decode("""
eNrtGEloFEGwenZmj5hkN4cGI8gQ8IpJNMbro2skUT9ZY3bBA0En2VaGbGaW2UkgPrzzFARfighBfYjg8fFAH/pTEBR9CIKQ
6DMvQcGHZq2u7YlJXCEBDUamlq7pqq5ruqqPnVcvK0aG79aOwjTYCgEYz0cgOInHsEU8IgY4ns+LrvcMY8v7MK8gEsZEBjV4
VPYiJHIo8j0KCtxRnyIG+IDtIGQhYVtcn0PYTjEYTMSwhalYewpcxlYOiymuSsI9hKsI3ybZx4S3Eecc4S0oO8IOwMt4on6z
rON9Sh2NlSNmcJ903hGnGRbCM1HHJ86zgqwGrY5pZObZwFK1FK4BpnQnt7gj6ChchxKAtrbGjo7G/QgjUI3pvgZf8jrAZ29d
P9F9/tzyGSD/61R+6Df8MCZxOv+CogKchPxhJop5CHfmKq2wHXcag33ccnNDsACeh0R1BGB3Jv0NalglrSGAJvCk9IyZc/Uj
tqMb2axjDxgZfaVpDeAIT+sux7G04RqrSoSC4w7qCaOPo83tZiajJ+yI7LUZLkfvnbuRVwbQ2mf3o2nX1tELSnfxPsPpzUUB
kv2WY+a4nnKMNHdyuO8kU2ua1zZXAexCQ66d1duto6bFuWNaRyPCZGOqPZlq3IRmWtMDhtXD8QW6zAHudHMrrSf7s9mMyYXt
rlyT3tywrqVh/YaNTZs2Yzwp2zUyYeGVhJygF1oprgjeYw/qSdd2eK5EHHG0JcambIlltFGUIk5DlPoVtF3EcJa/3fj0uqO7
M36IOCfpGCwclstEZiAPp4QGKpfTCBklq/WksZrwabK6hPq1hKtxLeNzeedC2dlxhmTO0uhy9NNC8Ca+YlJ/JfaHxvY8WDr0
Mb4K+7d2jh6rvvU2Pgx1eHinUV/8zkADa2CXLgp4GPeeTG6r7wkv/mWLDSsx+W55eSOIwnexqSBUEBYSCr3dVAmFJMScFChv
/gqUglRgggogpU5QKlLaBKUhFZR+hIY2zU+A/ISkfID8iHwcVwSl4PtdeaVISliuSxRsqUViVslWRNpSKa4SaUsl7bHhkBwT
cS2QcWlF4tLIVqm0pZF2mZQPFpEPkny59BYk+as3awJblUq4JyYOj7ifMBdVq0zg+Vq1StGqjcqcAM1OTMqzIvKM5CtkThjJ
i1mmnECxnPjggw8++ODDPwpMHuri4qTSRaRwdQjJ7zrfsY37n0n+W+gCG38u/qVsBwufDgzOqn4WgcY8W2yGOt73QgF70bsD
vdBNcfTOun7x6scmv8+MFWN/bgnN1v/4bOL8y/5/ACjfYpA=
"""))


def test(name, fn):
	try:
		fn()
		RESULTS.append((name, True, ""))
		print(f"  PASS  {name}")
	except Exception as e:  # noqa: BLE001 — a test harness reports every failure
		RESULTS.append((name, False, str(e)[:300]))
		print(f"  FAIL  {name} :: {type(e).__name__} {str(e)[:300]}")
	finally:
		calm()


def check(cond, msg="check failed"):
	if not cond:
		raise AssertionError(msg)


def eq(got, want, what="value"):
	if got != want:
		raise AssertionError(f"{what}: got {got!r}, expected {want!r}")


def refused(fn, sentence=None, exc=REFUSED):
	"""`fn` must be refused (with exactly `sentence` when given). A refused request leaves nothing behind,
	so whatever it wrote before the refusal is undone here as well; so is a request that was wrongly let
	through, to keep one failure from spilling into the scenarios after it."""
	frappe.db.savepoint(SP_REFUSED)
	try:
		fn()
	except exc as e:
		frappe.db.rollback(save_point=SP_REFUSED)
		frappe.clear_messages()
		if sentence is not None and str(e) not in ((sentence,) if isinstance(sentence, str) else sentence):
			raise AssertionError(f"refused, but with: {str(e)[:200]}") from None
		return
	except Exception:
		frappe.db.rollback(save_point=SP_REFUSED)
		raise
	frappe.db.rollback(save_point=SP_REFUSED)
	raise AssertionError("was not refused")


def unreadable(fn, sentence):
	"""`fn` must end as the reader's "this file cannot be read" with exactly this sentence."""
	try:
		fn()
	except reader.Unreadable as e:
		eq(str(e), sentence, "sentence")
		return e
	raise AssertionError("was read")


# ---- stand-ins for the queue, the Error Log, the AI and the transaction


class Fake:
	queued, logged, calls, asked, commits = [], [], [], [], []
	job_alive = {}  # job id -> the queue's answer; an id no test names (a real check) counts as still queued
	answer = None  # the AI's next answer: an outcome, or a function of the content that returns one
	settings_changed = False


class AsSeen:
	"""`frappe` as the job module sees it: the real module, except for the names given here."""

	def __init__(self, **swapped):
		self.__dict__.update(swapped)

	def __getattr__(self, name):
		return getattr(frappe, name)


def fake_enqueue(method, **kwargs):
	Fake.queued.append({"method": method, **kwargs})


def fake_log(title=None, message=None, *args, **kwargs):
	Fake.logged.append(frappe._dict(title=title, message=message))


def fake_alive(job):
	Fake.asked.append(job)
	answer = Fake.job_alive.get(job, True)
	if isinstance(answer, Exception):
		raise answer
	return answer


def fake_ai(system, content, schema, max_tokens=64000, effort="high", deadline_s=900):
	Fake.calls.append(frappe._dict(system=system, content=content, schema=schema))
	return Fake.answer(content) if callable(Fake.answer) else Fake.answer


def fake_commit():
	frappe.db.savepoint(SP_JOB)


def fake_rollback():
	frappe.db.rollback(save_point=SP_JOB)


def stand_in():
	ai.frappe = AsSeen(enqueue=fake_enqueue, log_error=fake_log)
	ai.background_jobs = frappe._dict(is_job_enqueued=fake_alive)
	ai.ai_call, ai.setup_problem, ai._finish = fake_ai, (lambda: ""), KEPT["_finish"]
	ai._commit, ai._rollback = fake_commit, fake_rollback


def calm():
	"""Back to the run's starting point after each test, whatever the test left behind."""
	frappe.set_user("Administrator")
	frappe.clear_messages()
	for record in (Fake.queued, Fake.logged, Fake.calls, Fake.asked, Fake.job_alive):
		record.clear()
	Fake.answer = None
	if ai:
		stand_in()
	if Fake.settings_changed:
		baseline()


# ---- files, built in memory


def listing(rows=2):
	"""A small invented payment list. The number keeps its bytes unlike any other fixture's: two files
	with the same bytes share one hash, and the same file cannot be checked twice at once."""
	n = next(SEQ)
	body = [[S1, f"ST/{n}/1", "", 1250.5, ""], [S2, "", f"PO-TEST-{n}", 800, "Advance"],
		[S3, f"RS/{n}/3", "", 300.25, ""]]
	return [HEAD, *body[:rows]]


def xlsx(rows=None, sheets=None):
	"""An Excel file: one sheet "Payments" of `rows`, or `sheets` given as (title, rows, hidden)."""
	from openpyxl import Workbook

	book = Workbook()
	book.remove(book.active)
	for title, sheet_rows, hidden in sheets or [("Payments", rows, False)]:
		sheet = book.create_sheet(title)
		for cells in sheet_rows:
			sheet.append(cells)
		if hidden:
			sheet.sheet_state = "hidden"
	out = io.BytesIO()
	book.save(out)
	return out.getvalue()


def csv_file(rows, delimiter=",", encoding="utf-8"):
	out = io.StringIO()
	csv.writer(out, delimiter=delimiter, lineterminator="\r\n").writerows(rows)
	return out.getvalue().encode(encoding)


def pdf(pages):
	"""A typed PDF written by hand: `pages` is one list of text lines per page (no brackets in the text)."""
	objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"", b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
	kids = []
	for lines in pages:
		text = "BT /F1 11 Tf 50 780 Td 14 TL\n" + "".join(f"({line}) Tj T*\n" for line in lines) + "ET"
		body = text.encode("latin-1")
		kids.append(len(objs) + 1)
		objs.append(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents %d 0 R "
			b"/Resources << /Font << /F1 3 0 R >> >> >>" % (len(objs) + 2))
		objs.append(b"<< /Length %d >>\nstream\n" % len(body) + body + b"\nendstream")
	objs[1] = b"<< /Type /Pages /Kids [%s] /Count %d >>" % (b" ".join(b"%d 0 R" % k for k in kids), len(kids))
	out, offsets = bytearray(b"%PDF-1.4\n"), []
	for number, obj in enumerate(objs, 1):
		offsets.append(len(out))
		out += b"%d 0 obj\n" % number + obj + b"\nendobj\n"
	xref = len(out)
	out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
	out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
	out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
	return bytes(out)


def typed_pdf(pages=1):
	"""A typed payment list as PDF; both amounts of page 1 are in its text layer."""
	mark = "".join(chr(ord("a") + int(digit)) for digit in str(next(SEQ)))  # no digit: it could match an amount
	first = [f"Payment list for approval, test {mark}", f"{S1} ST/1 1,25,000.00", f"{S2} HE/2 800.50",
		"Total 1,25,800.50"]
	return pdf([first, *([f"Continued on page {n}", f"{S3} RS/{n} 300.25"] for n in range(2, pages + 1))])


def locked_pdf(data):
	from pypdf import PdfReader, PdfWriter

	writer = PdfWriter()
	for page in PdfReader(io.BytesIO(data)).pages:
		writer.add_page(page)
	writer.encrypt("open-sesame")
	out = io.BytesIO()
	writer.write(out)
	return out.getvalue()


def picture(width, height, kind="JPEG", mode="RGB", **save):
	"""A flat picture; its shade changes with every call, so no two have the same bytes."""
	from PIL import Image

	n = next(SEQ)
	shade = (40 + n % 200, 250 - n % 200, 120) if mode == "RGB" else 0
	out = io.BytesIO()
	Image.new(mode, (width, height), shade).save(out, format=kind, **save)
	return out.getvalue()


def sent_picture(block):
	"""The picture inside an image block of the AI request."""
	from PIL import Image

	return Image.open(io.BytesIO(base64.b64decode(block["source"]["data"])))


def ole2(marker=b""):
	"""Bytes that start as old Office files do. With `marker` (the stream name Excel writes, as UTF-16)
	they look like a password-protected .xlsx, which is such a container and not a zip."""
	return bytes.fromhex("d0cf11e0a1b11ae1") + bytes(504) + marker + bytes(512)


def zipped(members):
	out = io.BytesIO()
	with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
		for name, body in members.items():
			archive.writestr(name, body)
	return out.getvalue()


def changed_xlsx(part, change):
	"""A real Excel file with one part rewritten by `change` (bytes -> bytes)."""
	src, out = zipfile.ZipFile(io.BytesIO(xlsx(listing()))), io.BytesIO()
	with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
		for item in src.infolist():
			body = src.read(item.filename)
			archive.writestr(item.filename, change(body) if item.filename == part else body)
	return out.getvalue()


def padded_xlsx(megabytes):
	"""A real Excel file that claims `megabytes` more inside than its own size shows (zeros pack small)."""
	out = io.BytesIO(xlsx(listing()))
	with zipfile.ZipFile(out, "a", zipfile.ZIP_DEFLATED) as archive:
		archive.writestr("xl/media/filler.bin", bytes(megabytes * 1024 * 1024))
	return out.getvalue()


# ---- what the AI stand-in answers


def outcome(data=None, **over):
	"""What the AI call hands back: the outcome keys of the build contract, all present."""
	out = frappe._dict(ok=True, text="", data=data, refused=False, cut=False, stop_reason="end_turn", model=MODEL,
		usage={"input_tokens": 1200, "output_tokens": 300, "cache_read_input_tokens": 40,
			"cache_creation_input_tokens": 60},
		cost=0.0221, error="", detail="", duration_s=1.5, refusal_category=None, fallback_ran=False)
	out.update(over)
	return out


def said(lines, **over):
	"""An AI answer in the reader's schema."""
	return {"is_payment_list": True, "problem": "", "pages_seen": 1, "printed_total": "", "lines": lines, **over}


def row(supplier, amount, page=1, **over):
	return {"page": page, "supplier": supplier, "bill_no": "", "bill_date": "", "po_no": "", "amount": amount,
		"remarks": "", "unclear": [], **over}


# ---- the site: users, files, submissions


def user(tag, roles):
	email = f"aichk.s1.{tag}@example.com"
	if frappe.db.exists("User", email):
		raise RuntimeError("a test user already exists on this site; this run touches no existing user")
	return frappe.get_doc({"doctype": "User", "email": email, "first_name": f"AICHK {tag}", "send_welcome_email": 0,
		"roles": [{"role": r} for r in roles]}).insert(ignore_permissions=True).name


def setting(**values):
	Fake.settings_changed = True
	for fieldname, value in values.items():
		frappe.db.set_single_value(SETTINGS, fieldname, value)


def baseline():
	"""Switched on, limits at their defaults, and a daily cap no demo day reaches."""
	setting(enabled=1, model="", max_checks_per_day=100000, max_file_mb=0, max_pages=0)
	Fake.settings_changed = False


def attach(who, data, ext):
	"""`data` as `who`'s own private upload, the File row the form's Attach button makes."""
	frappe.set_user(who)
	try:
		doc = frappe.get_doc({"doctype": "File", "file_name": f"{PREFIX}{next(SEQ):03d}.{ext}", "is_private": 1,
			"content": data}).insert(ignore_permissions=True)
	finally:
		frappe.set_user("Administrator")
	made = frappe._dict(name=doc.name, url=doc.file_url, path=doc.get_full_path())
	if os.path.basename(made.path).startswith(PREFIX):
		FILES_MADE.append(made.path)
	return made


def on_disk(made, data=None):
	"""The bytes of a test upload as the site holds them; with `data`, puts other bytes in their place."""
	check(os.path.basename(made.path).startswith(PREFIX), "not a file of this run")
	if data is not None:
		with open(made.path, "wb") as f:
			f.write(data)
	with open(made.path, "rb") as f:
		return f.read()


def draft(who, url, **over):
	"""A saved Draft made by `who`, as the form's Save makes it."""
	frappe.set_user(who)
	try:
		return frappe.get_doc({"doctype": SUB, "company": COMPANY, "list_date": frappe.utils.today(), "file": url,
			**over}).insert()
	finally:
		frappe.set_user("Administrator")


def submit(who, name, **sent_along):
	frappe.set_user(who)
	try:
		doc = frappe.get_doc(SUB, name)
		doc.update(sent_along)
		doc.submit()
		return doc
	finally:
		frappe.set_user("Administrator")


def sent(who, data, ext):
	"""`data` attached, saved and submitted by `who`: a check that waits for its job. Returns its name."""
	return submit(who, draft(who, attach(who, data, ext).url).name).name


def waiting(who):
	return sent(who, xlsx(listing()), "xlsx")


def run_job(name):
	"""The background job for one submission, run as its submitter (as Frappe's job runner does)."""
	frappe.set_user(frappe.db.get_value(SUB, name, "owner"))
	try:
		fake_commit()
		ai.run_check(name)
	finally:
		frappe.set_user("Administrator")


def state(name):
	return frappe.db.get_value(SUB, name, ["docstatus", "owner", *FORGED], as_dict=True)


def control(name):
	return dict(frappe.db.get_value(SUB, name, list(FORGED), as_dict=True))


def age(name, fieldname, minutes):
	"""Moves one of a submission's stamps into the past."""
	then = frappe.utils.now_datetime() - timedelta(minutes=minutes)
	frappe.db.set_value(SUB, name, fieldname, then, update_modified=False)


def spent(name):
	s = state(name)
	return {"model_used": s.model_used, "input_tokens": s.input_tokens, "output_tokens": s.output_tokens,
		"cost_usd": round(s.cost_usd, 4)}


def no_report(name):
	check(not frappe.db.exists(REPORT, name), "a report was left behind")
	eq(frappe.db.count(LINE, {"parent": name}), 0, "report lines left behind")


def can(who, doctype, ptype, doc=None, **kwargs):
	return bool(frappe.has_permission(doctype, ptype, doc=doc, user=who, **kwargs))


def run():
	global ai, reader, COMPANY
	frappe.init(site=SITE)
	frappe.connect()
	frappe.set_user("Administrator")
	# a saved User or File puts a search row on a queue that no rollback empties: off, for this process only
	frappe.local.conf.disable_global_search = 1
	from trustbit_ethanol.ts_gate_entry import ts_ai_check, ts_ai_check_reader

	ai, reader = ts_ai_check, ts_ai_check_reader
	KEPT.update({name: getattr(ai, name) for name in SWAPPED})
	ai.READS_PER_HOUR = 10 ** 6  # this run sends far more files in an hour than a person may; t_reads_per_hour tests it
	frappe.db.commit = lambda *args, **kwargs: Fake.commits.append(sys._getframe(1).f_code.co_name)
	users = frappe._dict()
	try:
		stand_in()
		COMPANY = frappe.db.get_value("Company", {}, "name")
		_run(users)
	finally:
		for name, value in KEPT.items():
			setattr(ai, name, value)
		del frappe.db.commit
		frappe.set_user("Administrator")
		frappe.db.rollback()  # each rolled-back File row also deletes its file from the disk
		tidy(users)
	failed = [r for r in RESULTS if not r[1]]
	for group in ("file", "read", "job", "perm"):
		rows = [r for r in RESULTS if r[0].startswith(group + ":")]
		print(f"  {group}: {sum(1 for r in rows if r[1])}/{len(rows)}")
	print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
	sys.exit(1 if failed else 0)


def tidy(users):
	"""What a rollback cannot undo: a test file still on the disk, and cache entries of the test users."""
	for path in FILES_MADE:
		if os.path.isfile(path):
			os.remove(path)
	for name in users.values():
		frappe.clear_cache(user=name)
	frappe.clear_document_cache(SETTINGS, SETTINGS)


def _run(u):
	live = frappe.db.count(SUB, {"docstatus": 1, "status": "Being checked"})
	if live:
		print(f"  note: {live} real check(s) are waiting on this site; a stale one stays locked until this run ends")
	u.update(acc=user("acc", ["Accounts User"]), acc2=user("acc2", ["Accounts User"]),
		mgr=user("mgr", ["Accounts Manager"]), md=user("md", ["MD"]), sm=user("sm", ["System Manager"]),
		it=user("it", ["IT Head"]), pur=user("pur", ["Purchase User"]))
	baseline()
	for label, group in (("file", _file_check), ("read", _reading), ("job", _job_life), ("perm", _permissions)):
		try:
			group(u)
		except Exception as e:  # noqa: BLE001 — one group's broken stage must not hide the other groups
			RESULTS.append((f"{label}: the group ran to its end", False, str(e)[:300]))
			print(f"  FAIL  {label}: the group stopped early :: {type(e).__name__} {str(e)[:300]}")
			calm()
	test("job: nothing in this run asked the database for a real commit", lambda: eq(Fake.commits, []))


def _file_check(u):
	"""Proves: Submit lets every kind of list through and refuses, in the contract's own sentence, a file
	the job could not read; a refused Submit leaves a Draft and queues nothing."""
	acc = u.acc

	def accepted(data, ext):
		made = attach(acc, data, ext)
		name = submit(acc, draft(acc, made.url).name).name
		s = state(name)
		eq((s.docstatus, s.status), (1, "Being checked"))
		eq(s.file_hash, hashlib.sha256(on_disk(made)).hexdigest(), "file hash")
		eq([q["job_id"] for q in Fake.queued], ["ts-ai-check-" + name], "queued jobs")
		return name

	def turned_away(data, ext, sentence):
		doc = draft(acc, attach(acc, data, ext).url)
		refused(lambda: submit(acc, doc.name), sentence)
		eq(control(doc.name), CLEAN, "the Draft after a refused Submit")
		eq((frappe.db.get_value(SUB, doc.name, "docstatus"), Fake.queued), (0, []))
		return doc.name

	kinds = (
		("an Excel file", lambda: xlsx(listing()), "xlsx"),
		("an old Excel file (.xls)", lambda: XLS, "xls"),
		("a CSV file", lambda: csv_file(listing()), "csv"),
		("a typed PDF", typed_pdf, "pdf"),
		("a scanned PDF (pictures only)", lambda: picture(600, 800, "PDF"), "pdf"),
		("a JPG photo", lambda: picture(1200, 900), "jpg"),
		("a PNG photo", lambda: picture(900, 1200, "PNG"), "png"),
	)
	for label, build, ext in kinds:
		test(f"file: {label} is accepted and waits for its check", lambda build=build, ext=ext: accepted(build(), ext))

	def t_empty():
		made = attach(acc, csv_file(listing()), "csv")
		doc = draft(acc, made.url)
		eq(on_disk(made, b""), b"")
		refused(lambda: submit(acc, doc.name), M6)
		eq(frappe.db.get_value(SUB, doc.name, "docstatus"), 0)
	test("file: an empty file is refused", t_empty)
	test("file: bytes of no known type are refused",
		lambda: turned_away(bytes(range(256)) * 4 + str(next(SEQ)).encode(), "dat", M5))
	test("file: a zip that is not an Excel file is refused",
		lambda: turned_away(zipped({"list.txt": f"{S1},1250.50,{next(SEQ)}"}), "zip", M5))

	def t_oversize():
		setting(max_file_mb=1)
		line = f"{S1},ST/{next(SEQ)},1250.50\r\n".encode()
		turned_away(b"Supplier Name,Bill No,Amount\r\n" + line * 50000, "csv", M4.format(1))
	test("file: a file over the size limit is refused before it is read", t_oversize)
	test("file: a PDF with a password is refused", lambda: turned_away(locked_pdf(typed_pdf()), "pdf", M7))
	test("file: an Excel file with a password is refused",
		lambda: turned_away(ole2("EncryptedPackage".encode("utf-16-le")), "xlsx", M7))

	def t_pages():
		setting(max_pages=3)
		turned_away(typed_pdf(pages=4), "pdf", M9.format(4, 3))
		accepted(typed_pdf(pages=3), "pdf")
	test("file: a PDF over the page limit is refused, one at the limit is not", t_pages)
	test("file: a photo of more than 40 million pixels is refused",
		lambda: turned_away(picture(8000, 5001, "PNG", mode="1"), "png", M10))
	test("file: a sheet with no supplier and amount headings is refused",
		lambda: turned_away(xlsx([["Name", "Value"], [S1, 1250.5], [f"note {next(SEQ)}", 1]]), "xlsx", M11))
	test("file: headings with no rows under them are refused",
		lambda: turned_away(xlsx([[f"Payment list {next(SEQ)}"], HEAD]), "xlsx", M12))

	def t_rows():
		n = next(SEQ)
		rows = [["Supplier Name", "Bill No", "Amount"], *([S1, f"ST/{n}/{i}", 10] for i in range(2001))]
		turned_away(csv_file(rows), "csv", M13A)
		eq(reader.basic_check(csv_file(rows[:-1]), ai.limits()).kind, "csv", "2,000 rows")
	test("file: a list of more than 2,000 rows is refused, 2,000 are not", t_rows)
	test("file: a list of more than 60 columns is refused", lambda: turned_away(
		xlsx([["Supplier Name", "Amount", *(f"Extra {i}" for i in range(59))], [S1, next(SEQ)]]), "xlsx", M13B))
	test("file: an Excel file that unpacks to more than 40 MB is refused",
		lambda: turned_away(padded_xlsx(41), "xlsx", M4B))

	def t_stall():
		# small files that would make the check read for minutes or fill the memory (security re-scan, 14 Nov)
		twice = changed_xlsx("xl/workbook.xml", lambda b: re.sub(
			rb'(<sheet [^>]*?)name="Payments"([^>]*/>)', rb'\1name="Payments"\2\1name="Again"\2', b, count=1))
		styles = changed_xlsx("xl/styles.xml", lambda b: b + b"<!--" + b"x" * 1100000 + b"-->")
		for data in (twice, styles):
			turned_away(data, "xlsx", M4B)
		started = frappe.utils.now_datetime()
		refused(lambda: reader.basic_check(b"Supplier Name,Amount" + b",x" * 3000000 + b"\r\n", ai.limits()), M13B)
		check((frappe.utils.now_datetime() - started).total_seconds() < 1, "a CSV row of 3 million cells read slowly")
	test("file: a small Excel or CSV made to stall the check is refused at once", t_stall)

	def t_own_process():
		# every read runs in its own process: a time limit, a memory limit (Linux), and our own fault logged
		listed = xlsx(listing())
		saved = reader.CHILD_SECONDS, reader.BOOT
		try:
			reader.CHILD_SECONDS = 0.01
			turned_away(listed, "xlsx", M28)
			reader.CHILD_SECONDS, reader.BOOT = saved[0], "import sys; sys.exit(3)"
			turned_away(listed, "xlsx", M29)
			eq(len(Fake.logged), 1, "Error Log rows for a reader process that failed")
		finally:
			reader.CHILD_SECONDS, reader.BOOT = saved
			Fake.logged.clear()
		if sys.platform.startswith("linux"):  # macOS does not cap a process's memory
			cells = b"<c/>" * 25
			bomb = changed_xlsx("xl/worksheets/sheet1.xml", lambda b: re.sub(
				rb"<sheetData>.*</sheetData>", b"<sheetData><row r=\"1\">" + b"&a;" * 400000 + b"</row></sheetData>",
				b.replace(b"<worksheet", b'<!DOCTYPE worksheet [<!ENTITY a "' + cells + b'">]><worksheet', 1), flags=re.S))
			started = frappe.utils.now_datetime()
			turned_away(bomb, "xlsx", (M28, M8))  # out of memory inside the XML parser reads as a parse error
			check((frappe.utils.now_datetime() - started).total_seconds() < reader.CHILD_SECONDS + 10, "too slow")
	test("file: reading runs in its own process with a time and a memory limit", t_own_process)

	def t_one_reader():
		# one reader per server (security re-check, 14 Nov): while another holds the slot, Submit waits, then says so
		import subprocess
		import time

		path = os.path.join(frappe.utils.get_bench_path(), "config", reader.SLOT)
		holder = subprocess.Popen([sys.executable, "-c", "import sys, time; from filelock import FileLock; "
			"lock = FileLock(sys.argv[1]); lock.acquire(); print('held', flush=True); time.sleep(30)", path], stdout=subprocess.PIPE)
		try:
			holder.stdout.readline()
			started = time.monotonic()
			turned_away(xlsx(listing()), "xlsx", M30)
			check(time.monotonic() - started < ai.SUBMIT_WAIT + 5, "waited too long")
		finally:
			holder.kill()
			holder.wait()
		accepted(xlsx(listing()), "xlsx")
	test("file: one file is read at a time on the server; Submit waits a little, then says the checker is busy",
		t_one_reader)

	def t_reads_per_hour():
		# refused Submits are not in the daily count, so each person may send READS_PER_HOUR files an hour
		key = frappe.cache.make_key("ts_ai_check_reads", user=acc)
		frappe.cache.delete(key)
		ai.READS_PER_HOUR = 2
		try:
			turned_away(csv_file([["Name", "Note"], ["a", "b"]]), "csv", M11)  # refused, and still counted
			accepted(xlsx(listing()), "xlsx")
			Fake.queued.clear()
			turned_away(xlsx(listing()), "xlsx", M31)
			check(3500 < frappe.cache.ttl(key) <= 3600, "the hour has no end")
		finally:
			ai.READS_PER_HOUR = 10 ** 6
			frappe.cache.delete(key)
	test("file: one person may send a set number of files an hour, refused ones included", t_reads_per_hour)

	def t_slot_error():
		saved = reader.SLOT
		try:
			reader.SLOT = "nginx.conf/slot.lock"  # its folder is a file: the slot cannot be made
			turned_away(xlsx(listing()), "xlsx", M29)
			eq(len(Fake.logged), 1, "Error Log rows")
		finally:
			reader.SLOT = saved
	test("file: a slot that cannot be taken is our side: Please tell IT, and logged", t_slot_error)

	def t_error_output():
		# the reader's error output goes to a file of at most 1 MB, never into this process; libraries stay quiet
		import resource
		import subprocess

		from openpyxl import Workbook
		from openpyxl.cell import WriteOnlyCell

		saved = reader.BOOT
		try:
			reader.BOOT = saved.replace("import runpy", "import sys\nfor _ in range(200):\n"
				"\tsys.stderr.write('x' * (1 << 20))\nimport runpy")
			before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
			if sys.platform.startswith("linux"):  # the kernel stops a reader that writes more than 1 MB of it
				turned_away(xlsx(listing()), "xlsx", M28)
			else:  # macOS may or may not stop it; either way the 200 MB must not come into this process
				try:
					reader.prepare(xlsx(listing()), ai.limits())
				except reader.Unreadable:
					pass
			grew = (resource.getrusage(resource.RUSAGE_SELF).ru_maxrss - before) / (1024 if sys.platform.startswith("linux") else 1 << 20)
			check(grew < 100, f"this process grew {grew:.0f} MB")
		finally:
			reader.BOOT = saved
		prelude = saved.split("import runpy")[0] + (
			"import resource, warnings, logging\n"
			"warnings.warn('x' * 50)\nlogging.getLogger('t').warning('y' * 50)\n"
			"print([resource.getrlimit(getattr(resource, n))[0] for n in ('RLIMIT_AS', 'RLIMIT_FSIZE', 'RLIMIT_CPU')])\n"
			"import os\nprint(open('/proc/self/oom_score_adj').read().strip() if os.path.exists('/proc/self/oom_score_adj') else '-')\n")
		done = subprocess.run([sys.executable, "-I", "-B", "-c", prelude], capture_output=True, timeout=30)
		eq(done.stderr, b"", "warnings or log lines written by the reader")
		if sys.platform.startswith("linux"):
			eq(done.stdout.decode().split("\n")[:2], [str([reader.CHILD_MB << 20, 1 << 20, 2 * reader.CHILD_SECONDS + 10]),
				"1000"], "the reader's limits and its place first in line if memory runs out")
		dates = Workbook(write_only=True)
		sheet = dates.create_sheet()
		for row in range(3000):  # 21,000 cells marked as dates with no date in them: openpyxl warns for each
			cells = []
			for k in range(7):
				cell = WriteOnlyCell(sheet, value=100000001 + row * 7 + k)
				cell.number_format = "yyyy-mm-dd"
				cells.append(cell)
			sheet.append([None] * 62 + cells)
		out = io.BytesIO()
		dates.save(out)
		turned_away(out.getvalue(), "xlsx", M11)  # the normal answer, not "too large": no warning was written
	test("file: the reader's error output stays small and outside the server process", t_error_output)

	def t_unopenable():
		turned_away(ole2(), "doc", M8)
		for broken in (b"%PDF-1.4\nnot a real PDF", picture(50, 50, "PNG")[:40], b"PK\x03\x04" + bytes(40)):
			refused(lambda broken=broken: reader.basic_check(broken, ai.limits()), M5 if broken[:2] == b"PK" else M8)
	test("file: a file that only looks like Office, PDF or a photo is refused", t_unopenable)

	other = attach(u.acc2, xlsx(listing()), "xlsx")
	test("file: a public file URL is refused at Save",
		lambda: refused(lambda: draft(acc, f"/files/{PREFIX}public.xlsx"), M2))
	test("file: another person's private upload is refused at Save",
		lambda: refused(lambda: draft(acc, other.url), M3))
	test("file: a private URL that no upload stands behind is refused at Save",
		lambda: refused(lambda: draft(acc, f"/private/files/{PREFIX}nothing.xlsx"), M3))

	def t_gone():
		made = attach(acc, xlsx(listing()), "xlsx")
		doc = draft(acc, made.url)
		on_disk(made)
		os.remove(made.path)
		refused(lambda: submit(acc, doc.name), M18)
	test("file: a file that left the disk after Save is refused at Submit", t_gone)

	def t_same_file():
		made = attach(acc, xlsx(listing()), "xlsx")
		first = submit(acc, draft(acc, made.url).name).name
		second = draft(acc, made.url)
		refused(lambda: submit(acc, second.name), M14)
		eq(state(first).status, "Being checked")
	test("file: the same file cannot be submitted while its first check is running", t_same_file)

	def t_dead_first():
		made = attach(acc, xlsx(listing()), "xlsx")
		first = submit(acc, draft(acc, made.url).name).name
		age(first, "started_at", 31)
		second = submit(acc, draft(acc, made.url).name).name
		eq((state(first).status, state(first).status_note), ("Check failed", M23))
		eq(state(second).status, "Being checked")
	test("file: a dead earlier check of the same file is closed first and does not block", t_dead_first)

	def t_switch():
		doc = draft(acc, attach(acc, xlsx(listing()), "xlsx").url)
		setting(enabled=0)
		refused(lambda: submit(acc, doc.name), M15)
		frappe.db.delete("Singles", {"doctype": SETTINGS, "field": "enabled"})
		refused(lambda: submit(acc, doc.name), M15)
		setting(enabled=1)
		eq(submit(acc, doc.name).status, "Being checked")
	test("file: with the switch off, or never set, Submit is refused", t_switch)

	def t_not_installed():
		doc = draft(acc, attach(acc, xlsx(listing()), "xlsx").url)
		ai.setup_problem = KEPT["setup_problem"]
		ai.frappe.get_installed_apps = lambda *args, **kwargs: ["frappe", "erpnext", "trustbit_ethanol"]
		eq(ai.setup_problem(), M16)
		refused(lambda: submit(acc, doc.name), M16)
		eq(frappe.db.get_value(SUB, doc.name, "docstatus"), 0)
	test("file: without the AI Staff app Submit is refused", t_not_installed)

	def t_no_key():
		from trustbit_ai_staff import guards, settings

		doc = draft(acc, attach(acc, xlsx(listing()), "xlsx").url)
		ai.setup_problem = KEPT["setup_problem"]
		ai.frappe.get_installed_apps = lambda *args, **kwargs: ["frappe", "trustbit_ethanol", "trustbit_ai_staff"]
		real = guards.app_enabled, settings.key
		try:
			guards.app_enabled, settings.key = (lambda: True), (lambda: "")
			eq(ai.setup_problem(), M16, "no key")
			refused(lambda: submit(acc, doc.name), M16)
			guards.app_enabled, settings.key = (lambda: False), (lambda: "not-a-real-key")
			eq(ai.setup_problem(), M16, "AI Staff switched off")
			guards.app_enabled = lambda: True
			eq(ai.setup_problem(), "", "ready")
			eq(submit(acc, doc.name).status, "Being checked")
		finally:
			guards.app_enabled, settings.key = real
	test("file: with AI Staff installed, no key or its switch off still refuses Submit", t_no_key)

	def t_daily_cap():
		doc = draft(acc, attach(acc, xlsx(listing()), "xlsx").url)
		midnight = frappe.utils.now_datetime().strftime("%Y-%m-%d 00:00:00")
		today = frappe.db.count(SUB, {"docstatus": 1, "submitted_at": (">=", midnight)})
		check(today > 0, "the earlier tests left no submitted list")
		setting(max_checks_per_day=today)
		refused(lambda: submit(acc, doc.name), M17.format(today))
		setting(max_checks_per_day=today + 1)
		eq(submit(acc, doc.name).status, "Being checked")
	test("file: at the day's limit Submit is refused; drafts do not count", t_daily_cap)

	def t_limits():
		for fieldname in ("enabled", "model", "max_checks_per_day", "max_file_mb", "max_pages"):
			frappe.db.delete("Singles", {"doctype": SETTINGS, "field": fieldname})
		Fake.settings_changed = True
		eq(dict(ai.limits()), {"enabled": False, "model": MODEL, "max_checks_per_day": 20, "max_file_mb": 10,
			"max_pages": 20}, "never saved")
		setting(enabled=1, model="  claude-test-model-1 ", max_checks_per_day=7, max_file_mb=50, max_pages=500)
		eq(dict(ai.limits()), {"enabled": True, "model": "claude-test-model-1", "max_checks_per_day": 7,
			"max_file_mb": 20, "max_pages": 100}, "over the ceilings")
	test("file: blank settings mean off and the defaults; stored values over the ceilings are cut", t_limits)


def _reading(u):
	"""Proves: the reader turns a sheet or an AI answer into the right lines, totals and notes, flags what
	it is not sure of, and never lets the AI's own words or a wrong shape through."""
	lim = frappe._dict(max_pages=20)

	def table(rows=None, **kwargs):
		return reader.read_table(xlsx(rows, **kwargs), "xlsx")

	def lines_of(out, *fields):
		return [tuple(line[f] for f in fields) for line in out.lines]

	def t_titles():
		out = table([["Payment list for approval"], ["Week 14"], [], HEAD, [S1, "ST/1", "", 1250.5, ""],
			[S2, "HE/2", "", 800, ""]])
		eq(out.notes, ["Sheet 'Payments' was read; its headings are on row 4.", "Amount column used: 'Amount'."])
		eq(lines_of(out, "line_no", "source_row", "page", "supplier_text", "amount", "amount_text"),
			[(1, 5, 0, S1, 1250.5, "1250.5"), (2, 6, 0, S2, 800.0, "800")])
		eq((out.file_kind, out.read_by, out.list_total, out.printed_total, out.pages_in_file, out.meta),
			("Excel", "Code", 2050.5, None, 0, {}))
		eq(set(out.lines[0]), set(LINE_FIELDS), "fields of a line")
	test("read: title rows above the headings are passed over", t_titles)

	def t_total():
		out = table([HEAD, [S1, "ST/1", "", 1000, ""], ["Total", None, None, 500, None], [S2, "HE/2", "", 250, ""],
			["GRAND TOTAL:", None, None, "1,250.00", None]])
		eq((len(out.lines), out.printed_total, out.list_total), (2, 1250.0, 1250.0))
		check(len(out.notes) == 2, f"a note too many: {out.notes}")
	test("read: a Total row is the printed total, not a line; the last one wins", t_total)

	def t_total_unlabelled():
		out = table([HEAD, [S1, "ST/1", "", 400, ""], [S2, "HE/2", "", 600, ""], ["Totals", None, None, 1000, None]])
		eq((len(out.lines), out.printed_total, out.list_total), (2, 1000.0, 1000.0), "labelled Totals")
		out = table([HEAD, [S1, "ST/1", "", 400, ""], [S2, "HE/2", "", 600, ""], [None, None, None, 1000, None]])
		eq((len(out.lines), out.printed_total, out.list_total), (2, 1000.0, 1000.0), "no label")
		out = table([HEAD, [S1, "ST/1", "", 400, ""], [S2, "HE/2", "", 600, ""], [None, None, None, 900, None]])
		eq(len(out.lines), 3, "a row that does not equal the lines stays a line")
	test("read: 'Totals', or a last row with no supplier equal to the lines, is the printed total", t_total_unlabelled)

	def t_subtotal():
		out = table([HEAD, [S1, "ST/1", "", 1000, ""], ["Sub Total", None, None, 1000, None],
			[None, "carried forward", None, None, None], [S2, "HE/2", "", 250, ""]])
		eq((len(out.lines), out.printed_total, out.list_total), (2, None, 1250.0))
		eq(out.notes[-1], "2 rows were skipped (sub-totals, or rows with no supplier and no amount).")
	test("read: a Sub Total row and a row with no supplier and no amount are skipped and counted", t_subtotal)

	item_head = ["Supplier Name", "Purchase Invoice ID", "Item Name", "Amount"]

	def t_items_equal():
		out = table([item_head, [S1, "PI-T-1", "Bearing", 5000], [S1, "PI-T-1", "Seal kit", 5000],
			[S1, "PI-T-1", "Gasket", 5000], [S2, "PI-T-2", "Valve", 800]])
		eq(lines_of(out, "source_row", "amount", "amount_text", "unclear", "read_note"),
			[(2, 5000.0, "5000", "amount", "Same amount on 3 rows; counted once."), (5, 800.0, "800", "", "")])
		eq(out.list_total, 5800.0)
	test("read: item rows of one bill with equal amounts are one line, counted once and flagged", t_items_equal)

	def t_items_differ():
		out = table([item_head, [S1, "PI-T-1", "Bearing", 1000], [S1, "PI-T-1", "Seal kit", 250.5]])
		eq(lines_of(out, "amount", "amount_text", "unclear", "read_note"),
			[(1250.5, "1000 + 250.5", "amount", "Amount is the sum of 2 rows.")])
	test("read: item rows of one bill with different amounts are summed and flagged", t_items_differ)

	def t_no_grouping():
		out = table([["Supplier Name", "Bill No", "Amount"], [S1, "ST/1", 5000], [S1, "ST/1", 5000]])
		eq(lines_of(out, "source_row", "amount", "unclear"), [(2, 5000.0, ""), (3, 5000.0, "")])
	test("read: without an item column two equal rows stay two lines", t_no_grouping)

	def t_formula():
		out = table([HEAD, [S1, "ST/1", "", "=1000*2", ""], [S2, "HE/2", "", 800, ""]])
		eq(lines_of(out, "amount", "amount_text", "unclear", "read_note"), [
			(0.0, "", "amount", "The amount is a formula that was never calculated."), (800.0, "800", "", "")])
	test("read: a formula nobody calculated reads as 0 and is flagged, not guessed", t_formula)

	def t_sheets():
		out = table(sheets=[("Old", [HEAD, ["Decoy Stores", "D/1", "", 1, ""]], True),
			("Cover", [["Prepared for the weekly meeting"]], False), ("Payments", [HEAD, *listing()[1:]], False),
			("Later", [HEAD, ["Decoy Stores", "D/2", "", 2, ""]], False)])
		eq(lines_of(out, "supplier_text"), [(S1,), (S2,)])
		eq(out.notes[0], "Sheet 'Payments' was read; its headings are on row 1.")
		eq(out.notes[2], "Sheets with rows that were not read: 'Old' (hidden), 'Cover', 'Later'.")
	test("read: a hidden sheet is never read; the other sheets with rows are named", t_sheets)

	def t_csv_cp1252():
		data = csv_file([["Party Name", "Bill No", "Amount (Rs.)"], ["Café Sunrise", "ST/1", "1,23,456.78"],
			[S2, "HE/2", "2,500.00"]], delimiter=";", encoding="cp1252")
		eq(reader.sniff(data), "csv")
		out = reader.read_table(data, "csv")
		eq(lines_of(out, "supplier_text", "amount", "amount_text"),
			[("Café Sunrise", 123456.78, "1,23,456.78"), (S2, 2500.0, "2,500.00")])
		eq((out.file_kind, out.notes), ("CSV", ["The headings are on row 1.", "Amount column used: 'Amount (Rs.)'."]))
	test("read: a Windows-encoded CSV with ';' between the cells keeps its commas inside the amounts", t_csv_cp1252)

	def t_csv_utf16():
		data = csv_file([["Vendor", "Invoice No", "Net Payable"], [S1, "ST/1", "₹ 2,500.00"], [S2, "HE/2", "800"]],
			delimiter="\t", encoding="utf-16")
		eq(reader.sniff(data), "csv")
		eq(lines_of(reader.read_table(data, "csv"), "supplier_text", "bill_no_text", "amount"),
			[(S1, "ST/1", 2500.0), (S2, "HE/2", 800.0)])
	test("read: a UTF-16 CSV with tabs (Excel's Unicode text) is read", t_csv_utf16)

	def t_xls():
		eq(reader.sniff(XLS), "xls")
		out = reader.read_table(XLS, "xls")
		eq(lines_of(out, "source_row", "kind", "supplier_text", "bill_no_text", "bill_date_text", "po_text",
			"amount_text", "amount"), [(4, "Bill", S1, "ST/101", "2026-04-07", "", "5000", 5000.0),
			(5, "Advance", S2, "", "", "PO-TEST-7", "1250.5", 1250.5),
			(6, "Other", S3, "", "", "", "Rs. 1,23,456.78", 123456.78)])
		eq((out.file_kind, out.printed_total, out.list_total), ("Excel 97-2003", 129707.28, 129707.28))
		eq(out.notes, ["Sheet 'Payments' was read; its headings are on row 3.", "Amount column used: 'Amount to Pay'.",
			"Sheets with rows that were not read: 'Old' (hidden)."])
	test("read: an old .xls gives dates as dates, whole numbers without .0, and skips its hidden sheet", t_xls)

	def amounts(pairs):
		for written, want in pairs:
			eq(reader.parse_amount(written), want, f"{written!r}")

	test("read: Indian and Western digit grouping are both amounts; other commas are not", lambda: amounts((
		("1,23,456.78", 123456.78), ("12,34,567", 1234567.0), ("1,234,567.50", 1234567.5), ("1234.5", 1234.5),
		(1500, 1500.0), (99.999, 100.0), ("12,34", None), ("1,2345", None), ("1,23,45", None), ("1.2.3", None))))
	test("read: Rs, INR, the rupee sign and /- around an amount are dropped", lambda: amounts((
		("Rs. 5,000/-", 5000.0), ("₹ 2,500.00", 2500.0), ("INR 750", 750.0), ("rs 1,000", 1000.0),
		("Rs.1,25,000.50/-", 125000.5))))
	test("read: brackets and a minus mean negative; a trailing Cr or Dr is dropped", lambda: amounts((
		("(1,234.00)", -1234.0), ("-500", -500.0), ("−250.50", -250.5), ("1,500.00 Cr", 1500.0),
		("750 Dr", 750.0))))
	test("read: text that is not plainly one number is no amount", lambda: amounts((
		("N/A", None), ("as per bill", None), ("", None), ("1000 2000", None), ("5000 approx", None), (True, None),
		(None, None), (1e13, None))))

	def t_kind():
		out = table([HEAD, [S1, "ST/1", "PO-TEST-1", 100, ""], [S2, "", "PO-TEST-7", 200, ""],
			[S3, "", "", 300, "Advance against order"], [S4, "", "", 400, "freight"]])
		eq(lines_of(out, "kind"), [("Bill",), ("Advance",), ("Advance",), ("Other",)])
	test("read: a bill number makes a Bill; a PO or 'adv' in the remarks an Advance; else Other", t_kind)

	def t_no_tags():
		out = table([HEAD, ['["<img src=x onerror=alert(1)>"]', "<b>ST/1</b>", "", 100, "<i>note</i>"]])
		texts = [value for line in out.lines for value in line.values() if isinstance(value, str)]
		check(texts and not any("<" in t or ">" in t for t in texts), "a < or > from the file was kept")
		check("img src=x onerror" in out.lines[0]["supplier_text"], "the text itself was lost")
	test("read: no < or > from the file reaches the report (the grid shows text as HTML)", t_no_tags)

	typed, two_pages, scan = typed_pdf(), typed_pdf(pages=2), picture(600, 800, "PDF")

	def read(data, answer):
		Fake.answer = answer if callable(answer) or "ok" in answer else outcome(answer)
		return reader.read_document(data, reader.basic_check(data, lim), lim, fake_ai)

	def t_request():
		out = read(typed, said([row(S1, "1,25,000.00", bill_no="ST/1"), row(S2, "800.50", po_no="PO-TEST-9"),
			row(S3, "300.25", remarks="adv. for spares"), row(S4, "10.00")]))
		eq(len(Fake.calls), 1, "AI calls")
		call = Fake.calls[0]
		check(call.system is reader.READ_SYSTEM and call.schema is reader.READ_SCHEMA, "prompt or schema")
		eq(call.content, [{"type": "document", "source": {"type": "base64", "media_type": "application/pdf",
			"data": base64.standard_b64encode(typed).decode("ascii")}},
			{"type": "text", "text": "Read this payment list. It has 1 page(s)."}])
		eq(lines_of(out, "line_no", "page", "source_row", "kind", "amount", "unclear"), [(1, 1, 0, "Bill", 125000.0, ""),
			(2, 1, 0, "Advance", 800.5, ""), (3, 1, 0, "Advance", 300.25, "amount"), (4, 1, 0, "Other", 10.0, "amount")])
		eq((out.file_kind, out.read_by, out.pages_in_file, out.pages_seen, out.meta),
			("PDF", f"AI ({MODEL})", 1, 1, SPENT))
		eq(set(out.lines[0]), set(LINE_FIELDS), "fields of a line")
	test("read: a PDF goes to the AI once, whole and first; the answer becomes lines with the cost", t_request)

	def t_cross_check():
		out = read(typed, said([row(S1, "1,25,000.00"), row(S2, "9,99,999.00")]))
		eq(lines_of(out, "amount", "unclear", "read_note"), [(125000.0, "", ""),
			(999999.0, "amount", "The amount was not found in the page's text.")])
	test("read: on a typed PDF an amount that is not in the page's own text is flagged", t_cross_check)

	def t_whole_numbers():
		# the page says 1,25,000.00 and 800.50: a misread that is PART of one of them is still flagged
		out = read(typed, said([row(S1, "25,000.00"), row(S1, "12,500"), row(S2, "800"), row(S2, "800.50"),
			row(S1, "1,25,000")]))
		eq(lines_of(out, "amount", "unclear"), [(25000.0, "amount"), (12500.0, "amount"), (800.0, "amount"),
			(800.5, ""), (125000.0, "")])
	test("read: an amount must be a whole number on the page, not part of one (1,500 is not in 15,000)",
		t_whole_numbers)

	def t_scan():
		out = read(scan, said([row(S1, "9,99,999.00")]))
		eq(lines_of(out, "amount", "unclear", "read_note"), [(999999.0, "", "")])
	test("read: a scanned PDF has no text to compare with, so nothing is flagged", t_scan)

	def t_photo():
		read(picture(3000, 1200, "PNG"), said([row(S1, "100")]))
		eq([(b["type"], b.get("source", {}).get("media_type")) for b in Fake.calls[0].content],
			[("image", "image/jpeg"), ("text", None)])
		eq(Fake.calls[0].content[1]["text"], "Read this payment list. It has 1 page(s).")
		image = sent_picture(Fake.calls[0].content[0])
		eq((image.format, image.size), ("JPEG", (2576, 1030)), "a wide photo")
		refused(lambda: reader.basic_check(picture(1024, 768), lim), SMALL_PHOTO)  # what upload "Optimize" leaves
		read(picture(1100, 700), said([row(S1, "100")]))
		eq(sent_picture(Fake.calls[1].content[0]).size, (1100, 700), "a small photo is not enlarged")
	test("read: a photo goes as one JPEG of at most 2,576 px; one of 1,024 px or less is refused", t_photo)

	def t_sideways():
		from PIL import Image

		exif = Image.Exif()
		exif[0x0112] = 6  # "turn me a quarter to the right": how a phone marks a photo taken upright
		out = read(picture(1200, 800, exif=exif), said([row(S1, "100")]))
		eq(sent_picture(Fake.calls[0].content[0]).size, (800, 1200))
		eq((out.file_kind, out.pages_in_file), ("Photo", 1))
	test("read: a photo the camera stored sideways is sent upright", t_sideways)

	def t_unclear():
		out = read(scan, said([row(S1, "100", unclear=["Supplier", "amount", "total", "page", "BILL_NO"]),
			row(S2, "illegible", unclear=["amount"])]))
		eq(lines_of(out, "unclear", "amount", "amount_text"),
			[("supplier, bill_no, amount", 100.0, "100"), ("amount", 0.0, "illegible")])
	test("read: the AI's unclear fields are kept, unknown names dropped, an unread amount is 0 and flagged", t_unclear)

	def stopped(answer, sentence, data=scan):
		e = unreadable(lambda: read(data, answer), sentence)
		eq(e.meta, SPENT, "what the call cost")

	test("read: an answer 'not a payment list' ends as could-not-be-read, lines or not", lambda: (
		stopped(said([], is_payment_list=False, problem="A delivery note."), M24),
		stopped(said([row(S1, "100")], is_payment_list=False), M24)))
	test("read: a payment list with no lines ends as could-not-be-read", lambda: stopped(said([]), M25))
	test("read: a refusal by the AI service ends as could-not-be-read",
		lambda: stopped(outcome(ok=False, refused=True, stop_reason="refusal", refusal_category="other"), M27))
	test("read: an answer cut off at the length limit ends as too-long",
		lambda: stopped(outcome(ok=False, cut=True, stop_reason="max_tokens", text='{"lines": ['), M26))
	test("read: more than 2,000 lines end as too-long",
		lambda: stopped(said([row(S1, "1")] * 2001), M26))

	def t_service():
		for answer, detail in ((outcome(ok=False, stop_reason=None, error="The AI service is busy.",
				detail="529 overloaded_error"), "529 overloaded_error"),
				(outcome(ok=False, stop_reason=None, error="The AI service is busy."), "The AI service is busy."),
				(None, "the AI call failed")):
			Fake.answer = answer
			try:
				reader.read_document(scan, reader.basic_check(scan, lim), lim, fake_ai)
			except reader.ServiceError as e:
				eq(e.detail, detail)
				if answer:
					eq(e.meta, SPENT, "what the call cost")
				continue
			raise AssertionError("no service error")
	test("read: a failed AI call is a service error that carries the detail for the log", t_service)

	def t_shape():
		good = said([row(S1, "100")])
		wrong = [None, [good], {k: v for k, v in good.items() if k != "printed_total"}, {**good, "pages_seen": "1"},
			{**good, "is_payment_list": 1}, said([{**row(S1, "100"), "amount": 100}]),
			said([{k: v for k, v in row(S1, "100").items() if k != "unclear"}]), said([row(S1, "100", unclear="amount")])]
		for data in wrong:
			Fake.answer = outcome(data)
			try:
				reader.read_document(scan, reader.basic_check(scan, lim), lim, fake_ai)
			except reader.ServiceError as e:
				eq(e.detail, "bad answer shape")
				continue
			raise AssertionError(f"accepted {str(data)[:80]}")
	test("read: an answer that does not have the schema's shape is a service error, never lines", t_shape)

	def t_pages_seen():
		out = read(two_pages, said([row(S1, "1,25,000.00", page=1), row(S2, "800.50", page=0),
			row(S3, "300.25", page=-4)], pages_seen=1))
		eq((out.pages_in_file, out.pages_seen), (2, 1))
		eq(out.notes, ["The reader saw 1 of 2 pages.", "No lines were read from these pages: 2."])
		out = read(two_pages, said([row(S1, "1,25,000.00"), row(S3, "300.25", page=9)], pages_seen=9))
		eq((lines_of(out, "page"), out.pages_seen, out.notes), ([(1,), (2,)], 2, []))
		eq(Fake.calls[0].content[1]["text"], "Read this payment list. It has 2 page(s).")
	test("read: pages the AI did not see or read are noted; page numbers stay inside the file", t_pages_seen)

	def t_printed():
		out = read(scan, said([row(S1, "1,000.00"), row(S2, "250.50")], printed_total="Rs. 9,999.00"))
		eq((out.printed_total, out.list_total), (9999.0, 1250.5))
		eq(out.notes, ["The lines add up to 1,250.50; the list's printed total is 9,999.00."])
		out = read(scan, said([row(S1, "1,000.00")], printed_total="see page 2"))
		eq((out.printed_total, out.notes), (None, ["The printed total could not be read as a number: 'see page 2'."]))
		out = read(scan, said([row(S1, "1,000.00")], printed_total="1,000.40"))
		eq((out.printed_total, out.notes), (1000.4, []))
	test("read: a printed total that differs from the lines, or is no number, is noted", t_printed)

	def t_long_answer():
		out = read(scan, said([row("S" * 300, "1" * 300, bill_no="B" * 300, bill_date="D" * 300, po_no="P" * 300,
			remarks="r" * 900)], problem="x" * 300))
		line = out.lines[0]
		eq([len(line[f]) for f in ("supplier_text", "bill_no_text", "bill_date_text", "po_text", "amount_text",
			"remarks_text")], [140, 140, 140, 140, 140, 500])
		eq((line["amount"], line["unclear"]), (0.0, "amount"))
		eq(out.notes[0], "The reader noted: " + "x" * 200)
	test("read: long AI texts are cut to their fields; the AI's remark goes to the notes, cut to 200", t_long_answer)


def _job_life(u):
	"""Proves: Submit queues exactly one job; the job makes one report or ends in the right status with
	nothing half-made left; a dead check is closed, a live or finished one is left alone."""
	acc = u.acc
	now = frappe.utils.now_datetime
	sheet = [["Payment list for approval"], HEAD, [S1, "ST/J/1", "", 1250.5, ""],
		[S2, "", "PO-TEST-J", 800, "Advance"], ["Total", None, None, 2050.5, None]]
	Fake.queued.clear()
	one = sent(acc, xlsx(sheet), "xlsx")
	queued = list(Fake.queued)

	def t_submit():
		s = state(one)
		eq((s.docstatus, s.status, s.status_note, s.started_at, s.finished_at), (1, "Being checked", None, None, None))
		check(s.submitted_at and abs((now() - s.submitted_at).total_seconds()) < 600, "submitted_at is not now")
		eq(queued, [{"method": "trustbit_ethanol.ts_gate_entry.ts_ai_check.run_check", "queue": "long",
			"timeout": 1200, "job_id": "ts-ai-check-" + one, "deduplicate": True, "enqueue_after_commit": True,
			"submission": one}], "the queue call")
	test("job: Submit sets Being checked and queues one job, after the commit, under the check's own id", t_submit)

	def t_report():
		run_job(one)
		s = state(one)
		eq((s.status, s.status_note, s.lines_read, s.model_used, s.input_tokens, s.output_tokens, s.cost_usd),
			("Report sent", None, 2, None, 0, 0, 0))
		check(s.started_at and s.finished_at and s.finished_at >= s.started_at, "start and finish stamps")
		eq(frappe.get_all(REPORT, filters={"submission": one}, pluck="name"), [one], "reports")
		r = frappe.get_doc(REPORT, one)
		check(r.company == COMPANY, "the company on the report")
		eq((r.owner, r.submitted_by, r.submitted_on, r.check_type, str(r.list_date)),
			(acc, acc, s.submitted_at, "Payment List", frappe.utils.today()))
		eq((r.file_kind, r.read_by, r.pages_in_file, r.pages_seen, r.lines_total, r.list_total, r.printed_total),
			("Excel", "Code", 0, 0, 2, 2050.5, 2050.5))
		eq(r.read_notes, "Sheet 'Payments' was read; its headings are on row 2.\nAmount column used: 'Amount'.")
		eq([tuple(line.get(f) or (0 if f in ("page", "amount") else "") for f in LINE_FIELDS) for line in r.lines], [
			(1, 0, 3, "Bill", S1, "ST/J/1", "", "", "1250.5", 1250.5, "", "", ""),
			(2, 0, 4, "Advance", S2, "", "", "PO-TEST-J", "800", 800.0, "Advance", "", "")])
		eq(Fake.calls, [], "AI calls for a sheet")
	test("job: a sheet ends as Report sent with one report, named like the submission, holding its lines", t_report)

	def t_again():
		before = state(one)
		run_job(one)
		eq(state(one), before)
		eq(frappe.db.count(REPORT, {"submission": one}), 1, "reports")
	test("job: a second run of the same job does nothing", t_again)

	def t_pdf():
		made = attach(acc, typed_pdf(), "pdf")
		name = submit(acc, draft(acc, made.url).name).name
		Fake.answer = outcome(said([row(S1, "1,25,000.00", bill_no="ST/1", unclear=["bill_date"]),
			row(S2, "800.50", po_no="PO-TEST-9")], printed_total="1,25,800.50"), cost=0.02214)
		run_job(name)
		eq(state(name).status, "Report sent")
		eq((state(name).lines_read, spent(name)), (2, SPENT))
		eq(len(Fake.calls), 1, "AI calls")
		eq(base64.b64decode(Fake.calls[0].content[0]["source"]["data"]), on_disk(made), "the bytes sent to the AI")
		r = frappe.get_doc(REPORT, name)
		eq((r.file_kind, r.read_by, r.pages_in_file, r.pages_seen, r.lines_total, r.list_total, r.printed_total),
			("PDF", f"AI ({MODEL})", 1, 1, 2, 125800.5, 125800.5))
		eq([(x.line_no, x.page, x.source_row, x.kind, x.amount, x.amount_text, x.unclear or "") for x in r.lines],
			[(1, 1, 0, "Bill", 125000.0, "1,25,000.00", "bill_date"), (2, 1, 0, "Advance", 800.5, "800.50", "")])
	test("job: a PDF is read by one AI call; the model, the tokens and the cost land on the submission", t_pdf)

	def t_unreadable():
		name = sent(acc, typed_pdf(), "pdf")
		Fake.answer = outcome(said([], is_payment_list=False, problem="A delivery note."))
		run_job(name)
		s = state(name)
		eq((s.status, s.status_note, s.lines_read), ("Could not be read", M24, 0))
		eq(spent(name), SPENT, "what the call cost")
		check(s.finished_at, "no finish stamp")
		no_report(name)
		eq(Fake.logged, [], "Error Log")
	test("job: a file the AI cannot read as a list ends as Could not be read, with the cost kept", t_unreadable)

	def t_service():
		name = sent(acc, typed_pdf(), "pdf")
		Fake.answer = outcome(ok=False, stop_reason=None, error="The AI service is busy.", detail="529 overloaded_error")
		run_job(name)
		eq((state(name).status, state(name).status_note), ("Check failed", M21))
		eq(spent(name), SPENT, "what the call cost")
		no_report(name)
		eq([(x.title, x.message) for x in Fake.logged], [("AI Checker: " + name, "529 overloaded_error")], "Error Log")
	test("job: a failed AI call ends as Check failed, with the technical detail in the Error Log only", t_service)

	def t_unexpected():
		name = waiting(acc)

		def stop_once(*args, **kwargs):
			ai._finish = KEPT["_finish"]
			check(frappe.db.exists(REPORT, name), "the report was not made before the failure")
			raise RuntimeError("stopped on purpose")
		ai._finish = stop_once
		run_job(name)
		s = state(name)
		eq((s.status, s.status_note, s.lines_read), ("Check failed", M22, 0))
		check(s.started_at and s.finished_at, "start and finish stamps")
		no_report(name)
		eq([x.title for x in Fake.logged], ["AI Checker: " + name], "Error Log titles")
		check("stopped on purpose" in Fake.logged[0].message, "the traceback is not in the Error Log")
	test("job: an unexpected error ends as Check failed; the half-made report is gone, the start stamp stays",
		t_unexpected)

	def t_reader_job():
		# a reader that fails or stays busy is our side, never "the AI service could not be reached"
		import subprocess

		saved = reader.BOOT, ai.JOB_WAIT
		name = waiting(acc)
		try:
			reader.BOOT = "import sys; sys.exit(3)"
			run_job(name)
			eq((state(name).status, state(name).status_note), ("Check failed", M22), "a reader that failed")
			reader.BOOT, ai.JOB_WAIT = saved[0], 0.2
			name = waiting(acc)
			path = os.path.join(frappe.utils.get_bench_path(), "config", reader.SLOT)
			holder = subprocess.Popen([sys.executable, "-c", "import sys, time; from filelock import FileLock; "
				"lock = FileLock(sys.argv[1]); lock.acquire(); print('held', flush=True); time.sleep(30)", path], stdout=subprocess.PIPE)
			try:
				holder.stdout.readline()
				run_job(name)
			finally:
				holder.kill()
				holder.wait()
			eq((state(name).status, state(name).status_note), ("Check failed", M22), "a reader that stayed busy")
			eq(len(Fake.logged), 2, "Error Log rows")
		finally:
			reader.BOOT, ai.JOB_WAIT = saved
			Fake.logged.clear()
	test("job: a reader that fails or stays busy ends as Check failed on our side, logged", t_reader_job)

	def t_file_deleted():
		made = attach(acc, xlsx(listing()), "xlsx")
		name = submit(acc, draft(acc, made.url).name).name
		frappe.db.delete("File", {"name": made.name})
		run_job(name)
		eq((state(name).status, state(name).status_note), ("Could not be read", M18))
		no_report(name)
		eq(Fake.logged, [], "Error Log rows for the submitter's own file problem")
	test("job: a file deleted after Submit ends as Could not be read, telling the submitter why", t_file_deleted)

	def t_file_changed():
		made = attach(acc, csv_file(listing()), "csv")
		name = submit(acc, draft(acc, made.url).name).name
		on_disk(made, csv_file(listing(3)))
		run_job(name)
		eq((state(name).status, state(name).status_note), ("Could not be read", M18))
		no_report(name)
	test("job: bytes that changed on the disk after Submit are not read", t_file_changed)

	def t_guard():
		doc = draft(acc, attach(acc, xlsx(listing()), "xlsx").url)
		frappe.set_user(acc)
		ai.run_check(doc.name)
		ai.run_check("AICHK-00-00000")
		eq(control(doc.name), CLEAN)
		no_report(doc.name)
	test("job: the job does nothing for a Draft or for a name that does not exist", t_guard)

	def closed(name):
		s = state(name)
		eq((s.status, s.status_note, s.lines_read), ("Check failed", M23, 0))
		check(s.finished_at and abs((now() - s.finished_at).total_seconds()) < 600, "finished_at is not now")

	def t_stale_running():
		name = waiting(acc)
		modified = frappe.db.get_value(SUB, name, "modified")
		age(name, "started_at", 31)
		eq(ai.sweep_stale(name), 1)
		closed(name)
		eq(frappe.db.get_value(SUB, name, "modified"), modified, "the last-edited stamp")
		eq(ai.sweep_stale(name), 0, "a second sweep")
	test("job: a check that started 31 minutes ago is closed by the sweep", t_stale_running)

	def t_fresh_running():
		name = waiting(acc)
		age(name, "started_at", 29)
		eq((ai.sweep_stale(name), state(name).status, Fake.asked), (0, "Being checked", []))
	test("job: a check that started 29 minutes ago is left alone", t_fresh_running)

	def t_stale_queued():
		name = waiting(acc)
		age(name, "submitted_at", 16)
		Fake.job_alive["ts-ai-check-" + name] = False
		eq(ai.sweep_stale(name), 1)
		closed(name)
		eq(Fake.asked, ["ts-ai-check-" + name] * 2, "the queue was asked before and after the lock")
	test("job: a check never started for 16 minutes, with no job in the queue, is closed", t_stale_queued)

	def t_still_queued():
		name = waiting(acc)
		age(name, "submitted_at", 16)
		eq(ai.sweep_stale(name), 0, "the queue still holds the job")
		Fake.job_alive["ts-ai-check-" + name] = ConnectionError("the queue cannot be asked")
		eq(ai.sweep_stale(name), 0, "the queue cannot be asked")
		Fake.job_alive["ts-ai-check-" + name] = False
		age(name, "submitted_at", 14)
		Fake.asked.clear()
		eq((ai.sweep_stale(name), Fake.asked), (0, []), "14 minutes")
		eq(state(name).status, "Being checked")
	test("job: a never-started check stays while the queue holds it, cannot be asked, or 15 minutes are not over",
		t_still_queued)

	def t_finished_untouched():
		age(one, "started_at", 120)
		age(one, "submitted_at", 120)
		Fake.job_alive["ts-ai-check-" + one] = False
		eq(ai.sweep_stale(one), 0)
		ai.sweep_stale()
		eq((state(one).status, state(one).status_note), ("Report sent", None))
	test("job: the sweep does not touch a finished check", t_finished_untouched)

	def t_finish_after_sweep():
		name = waiting(acc)
		age(name, "started_at", 31)
		eq(ai.sweep_stale(name), 1)
		eq(ai._finish(name, "Report sent", None, lines_read=3), False)
		closed(name)
	test("job: once a sweep closed a check, the job's own finish is refused", t_finish_after_sweep)

	def t_swept_while_reading():
		name = sent(acc, typed_pdf(), "pdf")

		def answer(content):
			age(name, "started_at", 31)
			eq(ai.sweep_stale(name), 1, "the sweep during the read")
			fake_commit()  # the sweep ran in another request, which committed
			return outcome(said([row(S1, "1,25,000.00")]))
		Fake.answer = answer
		run_job(name)
		closed(name)
		no_report(name)
	test("job: a check a sweep closed while the AI was reading keeps the sweep's result and gets no report",
		t_swept_while_reading)

	def t_killed():
		name = waiting(acc)
		frappe.db.set_value(SUB, name, "started_at", now(), update_modified=False)  # the job began; its worker died
		run_job(name)
		eq(state(name).status, "Being checked", "a second delivery of the job")
		no_report(name)
		frappe.set_user(acc)
		frappe.local.flags.commit = False
		try:
			doc = frappe.get_doc(SUB, name)
			doc.run_method("onload")
			eq((doc.status, bool(frappe.local.flags.commit)), ("Being checked", False), "opened at once")
			age(name, "started_at", 31)
			doc = frappe.get_doc(SUB, name)
			doc.run_method("onload")
			eq((doc.status, doc.status_note), ("Check failed", M23), "opened 31 minutes later")
			check(frappe.local.flags.commit is True, "a form load is rolled back unless it asks to be kept")
		finally:
			frappe.local.flags.commit = False
		closed(name)
	test("job: a check whose worker died is closed when its form is opened after 30 minutes", t_killed)

	def t_eta():
		setting(max_file_mb=7)
		frappe.db.savepoint("aichk_eta")
		try:
			names = frappe.get_all(SUB, filters={"owner": acc, "docstatus": 1}, pluck="name", limit=10)
			eq(len(names), 10, "submitted lists to borrow")
			for i, name in enumerate(names):
				end = frappe.utils.get_datetime("2099-01-01 10:00:00") + timedelta(minutes=i)
				frappe.db.set_value(SUB, name, {"status": "Report sent", "finished_at": end,
					"submitted_at": end - timedelta(seconds=90)}, update_modified=False)
			eq(ai.eta_minutes(), 2)
			frappe.set_user(acc)
			doc = frappe.get_doc(SUB, names[0])
			doc.run_method("onload")
			eq(dict(doc.get_onload()), {"eta_min": 2})
		finally:
			frappe.db.rollback(save_point="aichk_eta")
	test("job: the form is told how long the last ten checks took (rounded up)", t_eta)

	def t_ai_call():
		from trustbit_ai_staff import brain

		check(hasattr(brain, "call_once"), "the AI Staff app on this site has no brain.call_once (needs 0.7.0)")
		seen, real = [], brain.call_once
		brain.call_once = lambda **kwargs: seen.append(kwargs) or "the outcome"
		try:
			setting(model="claude-test-model-1")
			eq(KEPT["ai_call"]("the system text", ["the content"], {"type": "object"}), "the outcome")
		finally:
			brain.call_once = real
		eq(seen, [{"model": "claude-test-model-1", "system": "the system text", "content": ["the content"],
			"schema": {"type": "object"}, "max_tokens": 64000, "effort": "high", "deadline_s": 900,
			"lang": "English"}])
	test("job: the real AI call hands AI Staff the model from Settings and the fixed limits", t_ai_call)


def _permissions(u):
	"""Proves: a request can choose neither the name nor a control field; nothing submitted can be changed;
	Accounts see only their own lists and never a report; only the job writes reports."""
	acc, acc2 = u.acc, u.acc2
	today = frappe.utils.today()

	def own(who):
		return attach(who, xlsx(listing()), "xlsx").url

	a_draft, b_draft = draft(acc, own(acc)).name, draft(acc2, own(acc2)).name
	a_sent, b_sent, done = waiting(acc), waiting(acc2), waiting(acc)
	run_job(done)
	eq(state(done).status, "Report sent", "the stage's finished check")
	Fake.queued.clear()

	def t_name():
		doc = frappe.get_doc({"doctype": SUB, "name": "AICHK-PICKED", "owner": acc2, "company": COMPANY,
			"list_date": today, "file": own(acc)})
		doc.flags.name_set = True  # what the v2 API sets when a request carries a name
		frappe.set_user(acc)
		doc.insert()
		check(re.fullmatch(r"AICHK-\d\d-\d{5}", doc.name), f"named {doc.name}")
		eq((frappe.db.exists(SUB, "AICHK-PICKED"), frappe.db.get_value(SUB, doc.name, "owner")), (None, acc))
	test("perm: a new list gets its name from the counter and its owner from the session, not the request", t_name)

	test("perm: control fields sent with a new list are reset",
		lambda: eq(control(draft(acc, own(acc), **FORGED).name), CLEAN))

	def t_forged_save():
		frappe.set_user(acc)
		doc = frappe.get_doc(SUB, a_draft)
		doc.update(FORGED)
		doc.save()
		eq(control(a_draft), CLEAN)
	test("perm: control fields sent with a Save of a draft are reset", t_forged_save)

	def t_forged_submit():
		made = attach(acc, xlsx(listing()), "xlsx")
		name = submit(acc, draft(acc, made.url).name, **FORGED).name
		s = state(name)
		eq({**control(name), "submitted_at": None, "file_hash": None}, {**CLEAN, "status": "Being checked"})
		eq(s.file_hash, hashlib.sha256(on_disk(made)).hexdigest(), "file hash")
		check(s.submitted_at.year > 2001, "submitted_at came from the request")
	test("perm: control fields sent with the Submit are reset; hash and time come from the server", t_forged_submit)

	def t_insert_submitted():
		# refused outright since the 14 Nov re-check: it would keep the naming row locked through the read
		made = attach(acc, xlsx(listing()), "xlsx")
		refused(lambda: draft(acc, made.url, docstatus=1, **FORGED), M32)
		eq(frappe.db.count(SUB, {"file": made.url}), 0, "lists made by the refused call")
		eq(Fake.queued, [], "queued jobs")
	test("perm: Save and Submit in one call is refused (save first), forged control fields or not", t_insert_submitted)

	def t_no_change():
		for who in (acc, "Administrator"):
			frappe.set_user(who)
			doc = frappe.get_doc(SUB, a_sent)
			doc.list_date = "2020-01-01"
			refused(doc.save, NO_CHANGE)
			refused(frappe.get_doc(SUB, a_sent).save, NO_CHANGE)
		eq(str(frappe.db.get_value(SUB, a_sent, "list_date")), today)
	test("perm: a submitted list cannot be changed, not even by Administrator", t_no_change)

	def t_no_cancel():
		frappe.set_user(acc)
		refused(frappe.get_doc(SUB, a_sent).cancel, exc=DENIED)
		frappe.set_user("Administrator")
		refused(frappe.get_doc(SUB, a_sent).cancel, NO_CANCEL)
		refused(lambda: frappe.delete_doc(SUB, a_sent))
		eq(frappe.db.get_value(SUB, a_sent, "docstatus"), 1)
	test("perm: a submitted list cannot be cancelled or deleted", t_no_cancel)

	def t_no_amend():
		frappe.set_user(acc)
		copy = frappe.copy_doc(frappe.get_doc(SUB, a_sent))
		copy.amended_from = a_sent
		refused(copy.insert)
		eq(frappe.db.count(SUB, {"amended_from": a_sent}), 0)
	test("perm: a submitted list cannot be amended", t_no_amend)

	def t_own_only():
		both = {"name": ("in", [a_sent, b_sent, a_draft, b_draft])}
		frappe.set_user(acc)
		eq(sorted(frappe.get_list(SUB, filters=both, pluck="name")), sorted([a_sent, a_draft]), "first user's list")
		frappe.set_user(acc2)
		eq(sorted(frappe.get_list(SUB, filters=both, pluck="name")), sorted([b_sent, b_draft]), "second user's list")
		frappe.set_user(u.mgr)
		eq(frappe.get_list(SUB, filters=both, pluck="name"), [], "the Accounts Manager's list")
	test("perm: an Accounts user or manager lists only their own submissions", t_own_only)

	def t_not_anothers():
		from frappe import client

		for who in (acc, u.mgr):
			check(not can(who, SUB, "read", b_sent) and not can(who, SUB, "read", b_draft), f"{who} may read")
			frappe.set_user(who)
			refused(lambda: client.get(SUB, b_sent), exc=DENIED)
			refused(lambda: frappe.get_doc(SUB, b_draft).check_permission("read"), exc=DENIED)
		check(can(acc, SUB, "read", a_sent) and can(acc, SUB, "write", a_draft), "the owner's own rights")
		frappe.set_user(acc)
		eq(client.get(SUB, a_sent)["name"], a_sent)
	test("perm: an Accounts user cannot open another person's submission, only their own", t_not_anothers)

	def t_anothers_draft():
		frappe.set_user(acc2)
		doc = frappe.get_doc(SUB, a_draft)
		doc.list_date = "2020-01-01"
		refused(doc.save, exc=DENIED)
		for action in ("save", "submit"):
			doc = frappe.get_doc(SUB, a_draft)
			doc.owner = acc2  # "if owner" rights are judged on the owner the request sends
			refused(getattr(doc, action), exc=DENIED)
		refused(lambda: frappe.delete_doc(SUB, a_draft), exc=DENIED)
		eq(frappe.db.get_value(SUB, a_draft, ["owner", "docstatus", "list_date"]), (acc, 0, frappe.utils.getdate(today)))
	test("perm: another Accounts user cannot save, take over, submit or delete a draft", t_anothers_draft)

	def t_manager():
		name = waiting(u.mgr)
		eq((state(name).owner, state(name).status), (u.mgr, "Being checked"))
	test("perm: an Accounts Manager can save and submit an own list", t_manager)

	def t_readers():
		both = {"name": ("in", [a_sent, b_sent, a_draft, b_draft])}
		for who in (u.md, u.sm):
			url = own(who)
			frappe.set_user(who)
			eq(len(frappe.get_list(SUB, filters=both, pluck="name")), 4, f"{who} lists")
			check(can(who, SUB, "read", b_sent) and can(who, SUB, "read", a_draft), f"{who} may not read")
			refused(lambda: draft(who, url), exc=DENIED)
			doc = frappe.get_doc(SUB, a_draft)
			doc.list_date = "2020-01-01"
			frappe.set_user(who)
			refused(doc.save, exc=DENIED)
			refused(lambda: submit(who, a_draft), exc=DENIED)
			frappe.set_user(who)
			refused(lambda: frappe.delete_doc(SUB, a_draft), exc=DENIED)
		eq(frappe.db.get_value(SUB, a_draft, "docstatus"), 0)
	test("perm: MD and System Manager read every submission and can create, change or submit none", t_readers)

	def t_purchase():
		from frappe import client

		url = own(u.pur)
		refused(lambda: draft(u.pur, url), exc=DENIED)
		frappe.set_user(u.pur)
		refused(lambda: frappe.get_list(SUB), exc=DENIED)
		refused(lambda: client.get(SUB, a_sent), exc=DENIED)
		refused(lambda: frappe.get_list(REPORT), exc=DENIED)
		refused(lambda: client.get(SETTINGS), exc=DENIED)
		check(not any(can(u.pur, SUB, p, a_sent) for p in ("read", "write", "submit", "delete")), "a right too many")
		check(not can(u.pur, SUB, "create") and not can(u.pur, REPORT, "read", done), "a right too many")
	test("perm: a Purchase User can neither create nor read anything of the checker", t_purchase)

	def forged_report():
		return frappe.get_doc({"doctype": REPORT, "submission": a_sent, "lines_total": 1,
			"lines": [{"line_no": 1, "supplier_text": "forged", "amount": 1}]})

	def t_report_insert():
		refused(lambda: forged_report().insert(), JOB_ONLY)
		refused(lambda: forged_report().insert(ignore_permissions=True), JOB_ONLY)
		for who in (acc, u.sm, u.md):
			frappe.set_user(who)
			refused(lambda: forged_report().insert(), exc=DENIED)
			refused(lambda: forged_report().insert(ignore_permissions=True), JOB_ONLY)
		no_report(a_sent)
	test("perm: nobody can make a report by hand, with or without the permission check", t_report_insert)

	def t_report_change():
		for who in ("Administrator", u.sm, acc):
			frappe.set_user(who)
			doc = frappe.get_doc(REPORT, done)
			doc.read_notes, doc.lines[0].amount = "forged", 1
			doc.append("lines", {"line_no": 9, "supplier_text": "forged", "amount": 1})
			refused(doc.save, NO_REPORT_CHANGE if who == "Administrator" else None, exc=DENIED)
		frappe.set_user(u.sm)
		refused(lambda: frappe.delete_doc(REPORT, done), exc=DENIED)
		frappe.set_user("Administrator")
		r = frappe.get_doc(REPORT, done)
		eq((r.read_notes == "forged", len(r.lines), r.lines[0].amount), (False, 2, 1250.5))
	test("perm: a report cannot be changed by anyone, nor deleted by its reader", t_report_change)

	def t_rows_alone():  # REST /api/resource/<child>/<row> saves or deletes the row by itself
		row_name = frappe.db.get_value(LINE, {"parent": done, "line_no": 1})
		for who in ("Administrator", u.sm):
			frappe.set_user(who)
			line = frappe.get_doc(LINE, row_name)
			line.amount = 1
			refused(line.save, exc=DENIED)
			refused(line.delete, exc=DENIED)
			refused(lambda: frappe.get_doc({"doctype": LINE, "parent": done, "parenttype": REPORT, "parentfield": "lines",
				"line_no": 9, "supplier_text": "forged", "amount": 1}).insert(), exc=DENIED)
		frappe.set_user("Administrator")
		refused(frappe.get_doc(LINE, row_name).save, ROW_ALONE)
		eq(frappe.get_all(LINE, filters={"parent": done}, pluck="amount", order_by="idx"), [1250.5, 800.0])
	test("perm: a report line cannot be saved, added or deleted on its own", t_rows_alone)

	def t_report_hidden():
		from frappe import client

		row_name = frappe.db.get_value(LINE, {"parent": done, "line_no": 1})
		eq(frappe.db.get_value(REPORT, done, "owner"), acc, "the report's owner")
		for who in (acc, u.mgr, u.md):
			check(not can(who, REPORT, "read") and not can(who, REPORT, "read", done), f"{who} may read the report")
			check(not can(who, LINE, "read", parent_doctype=REPORT), f"{who} may read report lines")
			frappe.set_user(who)
			refused(lambda: frappe.get_list(REPORT, filters={"name": done}), exc=DENIED)
			refused(lambda: client.get(REPORT, done), exc=DENIED)
			refused(lambda: frappe.get_list(LINE, filters={"parent": done}, parent_doctype=REPORT), exc=DENIED)
			refused(lambda: client.get(LINE, row_name, parent=REPORT), exc=DENIED)
	test("perm: Accounts users and the MD cannot read a report or its lines, not even the submitter", t_report_hidden)

	def t_report_reader():
		from frappe import client

		check(can(u.sm, REPORT, "read", done) and not can(u.sm, REPORT, "write", done), "System Manager's rights")
		frappe.set_user(u.sm)
		eq(frappe.get_list(REPORT, filters={"name": done}, pluck="name"), [done])
		eq([x["supplier_text"] for x in client.get(REPORT, done)["lines"]], [S1, S2])
		eq(frappe.get_list(LINE, filters={"parent": done}, pluck="supplier_text", parent_doctype=REPORT,
			order_by="idx"), [S1, S2])
	test("perm: a System Manager reads a report and its lines", t_report_reader)

	def t_settings_roles():
		from frappe import client

		for who in (u.sm, u.it):
			check(can(who, SETTINGS, "read") and can(who, SETTINGS, "write"), f"{who} lacks a right")
		for who in (acc, u.mgr, u.md, u.pur):
			check(not can(who, SETTINGS, "read") and not can(who, SETTINGS, "write"), f"{who} has a right")
			frappe.set_user(who)
			refused(frappe.get_doc(SETTINGS).save, exc=DENIED)
			refused(lambda: client.get(SETTINGS), exc=DENIED)
		eq(ai.limits().max_file_mb, 10, "what a user without the right is still told through the form")
	test("perm: only System Manager and IT Head can read or change the Settings", t_settings_roles)

	def t_settings_clamp():
		Fake.settings_changed = True
		frappe.set_user(u.it)
		doc = frappe.get_doc(SETTINGS)
		doc.update({"enabled": 1, "model": "  claude-test-model-1 ", "max_checks_per_day": -5, "max_file_mb": 99,
			"max_pages": 500})
		doc.save()
		stored = frappe.db.get_singles_dict(SETTINGS)
		eq((stored.model, *(frappe.utils.cint(stored.get(f)) for f in ("max_checks_per_day", "max_file_mb", "max_pages"))),
			("claude-test-model-1", 0, 20, 100))
		doc = frappe.get_doc(SETTINGS)
		doc.update({"model": "", "max_file_mb": 20, "max_pages": 100})
		doc.save()
		eq(ai.limits().model, MODEL, "a blank model")
	test("perm: the Settings form trims the model, lifts negatives to 0 and cuts the two ceilings", t_settings_clamp)

	def t_model_name():
		Fake.settings_changed = True
		frappe.set_user(u.sm)
		for name in ("gpt-4", "claude", "claude-x y", "claude-OPUS-1", "claude-a;b", "claude-" + "a" * 61):
			doc = frappe.get_doc(SETTINGS)
			doc.model = name
			refused(doc.save, BAD_MODEL)
		eq(frappe.db.get_singles_dict(SETTINGS).get("model") or "", "")
	test("perm: a model name that is not a Claude model id is refused", t_model_name)

	def t_no_rename():
		import json

		from frappe import handler

		class Request:
			method = "POST"
		frappe.set_user(acc)
		refused(lambda: frappe.rename_doc(SUB, a_draft, a_draft + "-X"))
		old_request = getattr(frappe.local, "request", None)
		frappe.local.request, frappe.local.response = Request(), frappe._dict(docs=[])
		try:
			# Document.rename with validate_rename=false skips Frappe's own write and allow_rename checks
			# (until a Frappe that ignores the argument: then Frappe itself refuses, with another error)
			for who, doctype, name in ((acc, SUB, a_draft), (u.md, SUB, a_sent), (u.sm, REPORT, done)):
				frappe.set_user(who)
				refused(lambda: handler.run_doc_method(method="rename", dt=doctype, dn=name,
					args=json.dumps({"name": name + "-X", "validate_rename": False})))
				frappe.set_user("Administrator")
				check(frappe.db.exists(doctype, name) and not frappe.db.exists(doctype, name + "-X"), f"{doctype} renamed")
		finally:
			frappe.local.request = old_request
	# last on purpose: a rename first writes every user's cached list settings to the database, and those
	# rows stay locked until the transaction ends, which is now
	test("perm: neither a list nor a report can be renamed, by its owner or by a reader", t_no_rename)


if __name__ == "__main__":
	run()
