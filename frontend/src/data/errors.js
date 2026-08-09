// Frappe error-envelope handling. The server's _server_messages field is
// DOUBLE-JSON-encoded: json.dumps([json.dumps({...}) for each message]) —
// so it parses to an array of JSON *strings*, each of which parses to
// {message, title, indicator}. A generic client library shows "Request
// failed"; our executives must see "You cannot approve your own request."

const TAG_RE = /<[^>]*>/g

export function stripHtml(text) {
  return String(text || "").replace(TAG_RE, "").trim()
}

export function parseServerMessages(raw) {
  if (!raw) return []
  try {
    const outer = typeof raw === "string" ? JSON.parse(raw) : raw
    if (!Array.isArray(outer)) return []
    return outer
      .map((entry) => {
        try {
          const m = typeof entry === "string" ? JSON.parse(entry) : entry
          return {
            message: stripHtml(m.message),
            title: stripHtml(m.title || ""),
            indicator: m.indicator || "",
          }
        } catch {
          return { message: stripHtml(entry), title: "", indicator: "" }
        }
      })
      .filter((m) => m.message)
  } catch {
    return []
  }
}

export class FrappeError extends Error {
  constructor({ status, excType, messages, text }) {
    // Precedence: server messages → exception text → mapped type → status.
    const first = (messages && messages[0] && messages[0].message) || ""
    super(first || text || `Request failed (${status})`)
    this.name = "FrappeError"
    this.status = status
    this.excType = excType || ""
    this.messages = messages || []
    this.text = text || ""
  }

  get isPermissionError() {
    return this.status === 403 || this.excType === "PermissionError"
  }

  get isCSRFError() {
    return this.excType === "CSRFTokenError"
  }

  get isRateLimited() {
    return this.status === 429
  }

  // Human line for the action sheet — never the word "error", never a code.
  display() {
    if (this.isRateLimited) return "Too many attempts. Please wait a minute and try again."
    if (this.message && !/^Request failed/.test(this.message)) return this.message
    return "The server could not complete this. Please try again."
  }
}

export function errorFromResponse(status, body, rawText) {
  const messages = parseServerMessages(body && body._server_messages)
  let excType = ""
  if (body && body.exc_type) excType = body.exc_type
  let text = ""
  if (!messages.length && body && body.exception) {
    // "frappe.exceptions.ValidationError: actual text" → keep the tail
    const exc = String(body.exception)
    text = stripHtml(exc.includes(":") ? exc.split(":").slice(1).join(":") : exc)
  }
  if (!messages.length && !text && rawText) text = stripHtml(rawText).slice(0, 300)
  return new FrappeError({ status, excType, messages, text })
}
