"""A print format is rendered only on a document of its own DocType.

Frappe's print calls (`frappe.www.printview.get_html_and_style` and `get_rendered_raw_commands`) build the
document from the caller's own JSON, check read permission on the DocType that JSON names, and then render
whichever print format the caller named. A print format's Jinja reads the database without permission
checks, so a format could be run on a made-up document of any DocType the caller may read and made to look
records up for them (Lesson 457). The print page, both of those calls and every PDF or mail attachment made
from a format's own template end in `printview.get_rendered_template`; this puts one check in front of it.
"Standard" and a format with no DocType are left to Frappe. Not covered: a format made in the print format
builder (`print_format_builder_beta`), which `frappe.utils.weasyprint` renders from a saved document; this
app ships none. Applied from rename_guard.GUARDS (before_request and before_job).
"""

import frappe
from frappe import _
from frappe.www import printview as core

# Frappe's own function. Kept on Frappe's module, so a second import of this file (after `apply`) still
# finds Frappe's function and not the guard itself.
_core_render = getattr(core, "_ts_core_render", None) or core.get_rendered_template
core._ts_core_render = _core_render


def get_rendered_template(doc, print_format=None, *args, **kwargs):
	own = getattr(print_format, "doc_type", None)
	if own and own != doc.doctype:
		frappe.throw(_("Print format {0} is for {1}; it cannot print a {2}.").format(print_format.name, own, doc.doctype),
			frappe.PermissionError)
	return _core_render(doc, print_format, *args, **kwargs)


def apply():
	if core.get_rendered_template is not get_rendered_template:
		core.get_rendered_template = get_rendered_template
