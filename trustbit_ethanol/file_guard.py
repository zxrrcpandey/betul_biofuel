"""A File row may only point at a file its user can already read.

Frappe's disk steps (read, move between the private and public folders, delete, optimise) act on the address
stored in a File row. These checks make sure that a row's address, name, hash, thumbnail and attachment are
ones its user was entitled to set, and they match an address letter for letter (the address column compares
without case, accents or end spaces; the disk does not).

Applied by `rename_guard.apply` (before_request and before_job). Administrator (migrate, console, scheduler
work) is not checked. Why each check exists: the project's finding note of 23 Nov 2026 (kept outside this
repository). Remove a check only after Frappe itself enforces the same rule."""

import re
from urllib.parse import unquote

import frappe
from frappe import _
from frappe.core.doctype.file import utils as file_utils
from frappe.core.doctype.file.file import File
from frappe.utils import cint, cstr

SUBMISSION = "TS AI Check Submission"  # a checked list's file stays as it was checked
PLAIN = re.compile(r"/(private/)?files/[^/\\]+")  # the only address shape a move or delete is allowed on
_core = getattr(File, "_ts_core", None) or (File.before_insert, File.validate, File.optimize_file,
	File._delete_file_on_disk, file_utils.attach_files_to_document, File.get_full_path)
File._ts_core = _core


def _refuse(message=None):
	frappe.throw(message or _("You cannot change this file."), frappe.PermissionError)


def find_file_by_url(path, name=None):
	"""Core's own, with the address matched letter for letter."""
	path = cstr(path)
	filters = {"file_url": path, "name": cstr(name)} if name else {"file_url": path}
	for row in frappe.get_all("File", filters=filters, fields="*") if path else ():
		if row.file_url == path:
			file = frappe.get_doc(doctype="File", **row)
			if file.is_downloadable():
				return file


def _checked(url):
	return bool(url) and frappe.db.exists(SUBMISSION, {"file": url, "docstatus": 1})


def _check_new(doc):
	doc.is_folder = cint(doc.is_folder)  # always a number, so this test and core's agree
	if doc.is_folder:
		if doc.file_url or doc.thumbnail_url:
			_refuse()
		return
	doc.thumbnail_url = None  # core makes it
	if doc.get("content"):
		doc.file_url = None  # a real upload gets its address from core
		return
	doc.content_hash = None  # core works it out
	url = unquote(cstr(doc.file_url))  # as core will store it
	if not url:  # a row needs content or an address
		_refuse(_("The file is empty or was not received."))
	if ".." in url.replace("\\", "/").split("/"):  # an address stays inside its folder
		_refuse()
	# a private address, also inside a web address, must be a plain /private/files/<name> that the user can open
	if "/private/files/" in url and not (PLAIN.fullmatch(url) and find_file_by_url(url)):
		_refuse()


def _check_flip(doc, old):
	"""A private/public change moves the file for every row that shares it: each of those rows must be the
	user's to change, and the attached field must hold this address."""
	if doc.is_remote_file:
		return  # core moves nothing for a web address
	if _checked(old.file_url) or not PLAIN.fullmatch(cstr(old.file_url)):
		_refuse()
	same = {"file_url": old.file_url}  # rows of the same content share the file
	if old.content_hash:
		same["content_hash"] = old.content_hash
	for name in frappe.get_all("File", or_filters=same, pluck="name"):
		if not frappe.get_doc("File", name).has_permission("write"):
			_refuse()
	if doc.attached_to_doctype and doc.attached_to_name and doc.attached_to_field:
		meta = frappe.get_meta(doc.attached_to_doctype)
		if not meta.has_field(doc.attached_to_field):
			_refuse()
		held = (frappe.db.get_single_value(doc.attached_to_doctype, doc.attached_to_field) if meta.issingle
			else frappe.db.get_value(doc.attached_to_doctype, doc.attached_to_name, doc.attached_to_field))
		if cstr(held) != cstr(old.file_url):
			_refuse()


def _check_saved(doc):
	old = doc.get_doc_before_save()
	if not old:
		return
	doc.is_folder = cint(doc.is_folder)
	if doc.is_folder != cint(old.is_folder):
		_refuse()
	if old.is_folder:
		return
	if any(cstr(doc.get(f)) != cstr(old.get(f)) for f in ("file_url", "thumbnail_url", "content_hash")):
		_refuse()
	if _checked(old.file_url) and any(cstr(doc.get(f)) != cstr(old.get(f))
			for f in ("attached_to_doctype", "attached_to_name", "attached_to_field")):
		_refuse()
	if cint(doc.is_private) != cint(old.is_private):
		_check_flip(doc, old)


def before_insert(self):
	if frappe.session.user != "Administrator":
		_check_new(self)
	return _core[0](self)


def validate(self):
	if frappe.session.user != "Administrator" and not self.is_new():
		_check_saved(self)
	return _core[1](self)


@frappe.whitelist()
def optimize_file(self):
	"""Administrator only. The Optimize box of the upload dialog is another path and still works."""
	if frappe.session.user != "Administrator":
		_refuse()
	return _core[2](self)


def _delete_file_on_disk(self):
	"""The file on disk goes only with the last row of a plain address; otherwise only the row's own thumbnail
	data goes."""
	if frappe.session.user != "Administrator" and (not PLAIN.fullmatch(cstr(self.file_url)) or frappe.db.exists(
			"File", {"file_url": self.file_url, "name": ["!=", self.name]})):
		return self.delete_file_data_content(only_thumbnail=True)
	return _core[3](self)


def get_full_path(self):
	"""A saved row is read by its address, never by its name alone. An upload in progress has its content and no
	address yet; there core only asks whether the name is taken."""
	if not (self.file_url or self.get("content") or self.is_folder) and frappe.session.user != "Administrator":
		_refuse()
	return _core[5](self)


def attach_files_to_document(doc, event):
	"""A private address in an Attach field is linked to the saved document only when the user can open that
	file and no other row's address differs from it only by letter case. Any other value is hidden from core for
	this call and stays in the field as it was."""
	hidden = {}
	if frappe.session.user != "Administrator":
		for df in doc.meta.get("fields", {"fieldtype": ["in", ["Attach", "Attach Image"]]}):
			value = cstr(doc.get(df.fieldname))
			if value.startswith("/private/files") and not (find_file_by_url(value) and set(
					frappe.get_all("File", filters={"file_url": value}, pluck="file_url")) == {value}):
				hidden[df.fieldname] = doc.get(df.fieldname)
				doc.set(df.fieldname, None)
	try:
		return _core[4](doc, event)
	finally:
		for fieldname, value in hidden.items():
			doc.set(fieldname, value)


def apply():
	if File.get_full_path is not get_full_path:
		File.before_insert, File.validate, File.optimize_file = before_insert, validate, optimize_file
		File._delete_file_on_disk, File.get_full_path = _delete_file_on_disk, get_full_path
	if file_utils.find_file_by_url is not find_file_by_url:
		file_utils.attach_files_to_document = attach_files_to_document
		file_utils.find_file_by_url = find_file_by_url  # the download route takes it from the module at each call
