// Formatting helpers — Indian executives read lakh/crore, non-negotiable.

const inr = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  maximumFractionDigits: 0,
})

const inrPrecise = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
})

// "—" for missing/zero estimates: an exec reading "₹0.00" concludes "free".
export function money(value, { precise = false, dashOnEmpty = false } = {}) {
  const n = Number(value)
  if (!Number.isFinite(n) || (dashOnEmpty && n === 0)) return dashOnEmpty ? "—" : inr.format(0)
  return precise ? inrPrecise.format(n) : inr.format(n)
}

// Compact for cards: ₹18.40 L / ₹1.42 Cr
export function moneyCompact(value, { dashOnEmpty = false } = {}) {
  const n = Number(value)
  if (!Number.isFinite(n) || n === 0) return dashOnEmpty ? "—" : "₹0"
  const abs = Math.abs(n)
  if (abs >= 1e7) return `₹${(n / 1e7).toFixed(2)} Cr`
  if (abs >= 1e5) return `₹${(n / 1e5).toFixed(2)} L`
  return inr.format(n)
}

// "2 h at your step" style ages from a datetime string (server IST).
export function age(datetimeStr) {
  if (!datetimeStr) return ""
  const then = new Date(String(datetimeStr).replace(" ", "T"))
  if (Number.isNaN(then.getTime())) return ""
  const mins = Math.max(1, Math.floor((Date.now() - then.getTime()) / 60000))
  if (mins < 60) return `${mins} min`
  const hours = Math.floor(mins / 60)
  if (hours < 24) return `${hours} h`
  const days = Math.floor(hours / 24)
  return `${days} d`
}

export function shortDate(dateStr) {
  if (!dateStr) return ""
  const d = new Date(String(dateStr).replace(" ", "T"))
  if (Number.isNaN(d.getTime())) return String(dateStr)
  return d.toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" })
}

// Status pill mapping — a TOTAL function. role_label is free text the engine
// interpolates into statuses ("Pending {role_label}"), so NEW strings can
// appear without a deploy: rule-based with a guaranteed verbatim fallback —
// never blank, never throw.
export function statusMeta(status) {
  const s = String(status || "").trim()
  if (!s) return { label: "—", tone: "neutral" }
  if (s === "Approved" || s === "Active") return { label: s, tone: "ok" }
  if (s === "Rejected" || s === "Expired") return { label: s, tone: "danger" }
  // Informational blue — a revised doc is awaiting the submitter, not us.
  if (s === "Revised") return { label: s, tone: "info" }
  if (s.startsWith("On Hold")) return { label: "On Hold", tone: "warn" }
  // Blocked purple — NOBODY in the chain can act while the override pends.
  if (s === "Pending Budget Override") return { label: s, tone: "blocked" }
  if (s.startsWith("Pending") || s.startsWith("Awaiting")) return { label: s, tone: "warn" }
  return { label: s, tone: "neutral" }
}

export const DOCTYPE_META = {
  "Purchase Order": { tag: "PURCHASE ORDER", short: "PO", tone: "ok" },
  "Material Request": { tag: "MATERIAL REQUEST", short: "MR", tone: "info" },
  "TS Budget Override Approval": { tag: "BUDGET OVERRIDE", short: "Budget", tone: "blocked" },
  "TS Post Dated Entry Request": { tag: "POST-DATED", short: "Post-Dated", tone: "warn" },
}
