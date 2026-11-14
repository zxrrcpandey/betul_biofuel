"""Switch and limits of the AI Checker. No field carries a JSON default, because a Single's JSON default
overwrites the stored value on every migrate. A blank therefore means what `ts_ai_check.limits` gives
it, and this form only keeps out what that function could not use."""

import re

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint

MODEL_NAME = re.compile(r"^claude-[a-z0-9.-]{2,60}$")
LIMITS = ("max_checks_per_day", "max_file_mb", "max_pages")
# `ts_ai_check.limits` cuts these two to the same ceilings, so the form must not show a larger number
CEILINGS = {"max_file_mb": 20, "max_pages": 100}


class TSAICheckSettings(Document):
	def validate(self):
		self.model = (self.model or "").strip()
		if self.model and not MODEL_NAME.match(self.model):
			frappe.throw(_("The model name is not valid."))
		for fieldname in LIMITS:
			value = max(cint(self.get(fieldname)), 0)
			self.set(fieldname, min(value, CEILINGS.get(fieldname, value)))
