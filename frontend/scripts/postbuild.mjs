// Post-build pipeline: relocate the Vite shell into www/exec.html with the
// Jinja/CSRF markers, stamp + place the service worker and manifest, copy
// icons, and enforce the build-time safety assertions.
import { createHash } from "node:crypto"
import fs from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"

const here = path.dirname(fileURLToPath(import.meta.url))
const FRONTEND = path.resolve(here, "..")
const APP_PKG = path.resolve(FRONTEND, "..") // apps/trustbit_ethanol
const MODULE = path.join(APP_PKG, "trustbit_ethanol")
const OUT = path.join(MODULE, "public", "exec")
const WWW = path.join(MODULE, "www")
const WWW_EXEC = path.join(WWW, "exec")

function fail(msg) {
  console.error(`\npostbuild FAILED: ${msg}\n`)
  process.exit(1)
}

// ── ASSERTION 1: no package.json at the app root, EVER.
// frappe's esbuild runs `yarn build` in any app root that has one, and the
// failure aborts `bench build` on production.
if (fs.existsSync(path.join(APP_PKG, "package.json"))) {
  fail("package.json exists at the APP ROOT — this would abort bench build on production. It must live only in frontend/.")
}

// ── ASSERTION 2: no v-html anywhere in the source (stored-XSS vector —
// role_label is free text).
const srcDir = path.join(FRONTEND, "src")
const offenders = []
;(function scan(dir) {
  for (const f of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, f.name)
    if (f.isDirectory()) scan(p)
    else if (/\.(vue|js)$/.test(f.name) && fs.readFileSync(p, "utf8").includes("v-html"))
      offenders.push(p)
  }
})(srcDir)
if (offenders.length) fail(`v-html found in: ${offenders.join(", ")}`)

// ── Locate the built shell + main bundle
const builtIndex = path.join(OUT, "index.html")
if (!fs.existsSync(builtIndex)) fail("built index.html not found — did vite build run?")
const assetsDir = path.join(OUT, "assets")
const bundle = fs.readdirSync(assetsDir).find((f) => /^index-.+\.js$/.test(f))
if (!bundle) fail("hashed index-*.js bundle not found")
const swVersion = createHash("sha256")
  .update(fs.readFileSync(path.join(assetsDir, bundle)))
  .digest("hex")
  .slice(0, 12)

// ── Shell → www/exec.html with Jinja/CSRF markers
let html = fs.readFileSync(builtIndex, "utf8")
if (!html.includes("<!-- exec:jinja-head -->")) fail("exec:jinja-head marker missing from built index.html")
html = html.replace(
  "<!-- exec:jinja-head -->",
  [
    // Shim first: the injected csrf snippet assigns frappe.csrf_token and this
    // standalone page has no global `frappe` object of its own.
    // NOTE: no ".__" sequences allowed anywhere in this template — frappe's
    // safe_render rejects the whole page as "Illegal template" (dunder guard).
    "<script>window.frappe = window.frappe || {};</script>",
    "<!-- csrf_token -->",
    "<script>window.execEnv = { enabled: {{ exec_enabled }}, sw: {{ exec_sw }}, overview: {{ exec_overview }} };</script>",
    "<!-- no-cache -->",
  ].join("\n    ")
)
if (!html.includes("<!-- csrf_token -->")) fail("csrf_token marker missing after transform")
if (!html.includes("<!-- no-cache -->")) fail("no-cache marker missing after transform")
// Every execEnv key the app reads must be a Jinja placeholder here. www/exec.html
// is GENERATED — hand-editing it looks like it works until the next build
// silently reverts it, and the tab then never appears for anyone.
for (const v of ["exec_enabled", "exec_sw", "exec_overview"]) {
  if (!html.includes(`{{ ${v} }}`)) fail(`execEnv placeholder {{ ${v} }} missing from exec.html`)
}
// frappe safe_render rejects the whole page as "Illegal template" if the
// Jinja source contains ".__" anywhere (dunder-attack guard).
if (html.includes(".__")) fail('".__" found in exec.html — frappe safe_render will refuse to render it')
fs.writeFileSync(path.join(WWW, "exec.html"), html)
fs.rmSync(builtIndex) // never ship the raw shell under /assets

// ── ASSERTION: no Tailwind opacity modifier over a var()-backed token colour.
// Every colour in tailwind.config.js resolves to `var(--exec-*)`, and Tailwind
// v3 cannot compute alpha over that — it emits NO rule at all, so the element
// silently loses the property. It fails at RUNTIME, invisibly, and only for
// the one class you got wrong. Literal palette colours (bg-black/45) are fine.
{
  const TOKENS = "brand|ink|surface|ok|warn|danger|info|blocked"
  const bad = []
  const walk = (dir) => {
    for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
      const p = path.join(dir, e.name)
      if (e.isDirectory()) walk(p)
      else if (/\.(vue|js)$/.test(e.name)) {
        const re = new RegExp(`(?:bg|text|border|ring|fill|stroke)-(?:${TOKENS})(?:-[a-z]+)?\\/\\d+`, "g")
        for (const m of fs.readFileSync(p, "utf8").matchAll(re)) {
          bad.push(`${path.relative(FRONTEND, p)}: ${m[0]}`)
        }
      }
    }
  }
  walk(path.join(FRONTEND, "src"))
  if (bad.length) {
    fail(`Tailwind opacity modifier over a var() token colour compiles to nothing:\n  ${bad.join("\n  ")}\n  Use an inline style with the CSS variable, or a solid token.`)
  }
}

// ── Service worker → www/exec/sw.min.js (raw-served, scope /exec/)
fs.mkdirSync(WWW_EXEC, { recursive: true })
const sw = fs
  .readFileSync(path.join(FRONTEND, "src", "sw.js"), "utf8")
  .replace("__SW_VERSION__", swVersion)
if (sw.includes("__SW_VERSION__")) fail("SW version stamp failed")
fs.writeFileSync(path.join(WWW_EXEC, "sw.min.js"), sw)

// ── ASSERTION 3: the SW must never cache or intercept /api
if (!/pathname\.startsWith\("\/api\/"\)\) return/.test(sw)) {
  fail("service worker /api early-return guard missing")
}

// ── Manifest → www/exec/manifest.webmanifest (NOT /assets — that path
// carries ~1-year immutable caching and a pinned manifest is a trap)
fs.copyFileSync(
  path.join(FRONTEND, "manifest.webmanifest"),
  path.join(WWW_EXEC, "manifest.webmanifest")
)

// ── Icons → public/exec/icons (emptyOutDir wipes them every build)
const iconSrc = path.join(FRONTEND, "icons")
const iconDst = path.join(OUT, "icons")
if (!fs.existsSync(iconSrc)) fail("frontend/icons/ missing — generate the icon set first")
fs.mkdirSync(iconDst, { recursive: true })
for (const f of fs.readdirSync(iconSrc)) {
  fs.copyFileSync(path.join(iconSrc, f), path.join(iconDst, f))
}
const required = [
  "icon-192.png",
  "icon-512.png",
  "icon-512-maskable.png",
  "apple-touch-icon-180.png",
  "favicon-32.png",
]
for (const f of required) {
  if (!fs.existsSync(path.join(iconDst, f))) fail(`required icon missing: ${f}`)
}

// ── ASSERTION 4: no credentials or resource-API usage in the shipped bundle
const bundleText = fs.readFileSync(path.join(assetsDir, bundle), "utf8")
for (const needle of ["api_key", "api_secret", "Authorization: token", "/api/resource", "client.set_value"]) {
  if (bundleText.includes(needle)) fail(`forbidden string in shipped bundle: ${needle}`)
}

console.log(`postbuild OK — sw version ${swVersion}, bundle ${bundle}`)
