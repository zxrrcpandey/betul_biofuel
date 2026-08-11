# Copyright (c) 2026, Trustbit Software and contributors
# ALL executive-PWA switches in ONE independent Singles doctype — user
# decision 12 Aug 2026 (consolidated out of TS Settings; WhatsApp-Settings
# precedent). Access is the ADMINISTRATOR ACCOUNT ONLY (user decision,
# 12 Aug 2026: "only Administrator have access and nobody else"): the sole
# permission row grants the "Administrator" ROLE, which ships with Frappe,
# has zero holders, and is implicitly held only by the Administrator user
# (who holds every role). A role row must exist or the desk form renders
# read-only even for Administrator — learned live 12 Aug. Granting the
# Administrator role to a user would widen this; treat that as forbidden.
#
# ⚠ NO field carries a JSON `default` — L227: a Singles JSON default
# OVERWRITES the stored value on every migrate. Values are seeded ONCE by
# setup_exec_pwa._migrate_switch_values (INSERT-ONLY, copying any existing
# TS Settings value first), and the readers' fail-open/fail-closed semantics
# cover a missing row exactly as documented per field.

from frappe.model.document import Document


class TSExecAppSettings(Document):
    pass
