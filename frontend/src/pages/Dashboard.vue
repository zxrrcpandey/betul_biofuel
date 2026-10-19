<template>
  <div class="pb-28">
    <AppHeader title="Dashboard" bell refreshable :refreshing="dashboard.loading" @refresh="reload" />
    <AlertsSheet />

    <!-- Sticky and OUTSIDE PullToRefresh, which moves its content with a
         transform — nothing inside it can stick. -->
    <div class="sticky z-20 bg-surface-page px-4 py-2" style="top: env(safe-area-inset-top, 0px)">
      <div class="grid grid-cols-5 gap-1" role="group" aria-label="Period">
        <button
          v-for="per in PERIODS"
          :key="per.key"
          type="button"
          class="min-h-secondary rounded-xl border text-[13px] font-semibold"
          :class="per.key === dashboard.period ? 'border-brand-deep bg-brand-deep text-white' : 'border-surface-line bg-surface text-ink-muted'"
          :aria-pressed="per.key === dashboard.period"
          @click="loadDashboard(per.key)"
        >{{ per.label }}</button>
      </div>
      <p class="tnum mt-2 text-[13px]" :class="stale && !dashboard.error ? 'text-warn-text' : 'text-ink-faint'">{{ asOfLine }}</p>
    </div>

    <PullToRefresh :refreshing="dashboard.loading" @refresh="reload">
      <div class="flex flex-col gap-3 px-4" :class="{ 'opacity-60': dashboard.loading && d }" :aria-busy="dashboard.loading ? 'true' : 'false'">
        <!-- Older figures kept after a failed load must say so (Overview's rule). -->
        <div v-if="dashboard.error && d" class="rounded-xl bg-warn-bg px-3 py-2 text-[13px] text-warn-text" role="status">
          <template v-if="dashboard.failedPeriod">
            Couldn't load {{ periodOf(dashboard.failedPeriod).label }} — still showing {{ periodOf(d.filters.date_range).label }}.
          </template>
          <template v-else>Couldn't refresh just now — showing the figures from {{ asOfTime }}.</template>
          {{ dashboard.error.display() }}
        </div>
        <div v-if="d && d.confidential_scope === 'partial'" class="rounded-xl bg-blocked-bg px-3 py-2 text-[13px] text-blocked-text">
          Partial view — confidential purchase orders are left out of these figures for your account.
        </div>

        <StatCard v-if="!d && dashboard.error && !dashboard.loading" title="Dashboard" state="error" :error-detail="dashboard.error.display()" @retry="reload" />
        <template v-else>
          <StatCard v-if="show('waiting')" title="Waiting for me" :scope="waitingScope" :state="st('waiting')" :error-detail="SECTION_ERR" @retry="reload">
            <template v-if="w">
              <div v-if="!w.total" class="py-2 text-[17px] font-bold">Nothing waiting</div>
              <template v-else>
                <div class="tnum text-[38px] font-bold leading-none">{{ w.total }}</div>
                <p class="tnum mt-2 text-[14px] text-ink-muted">{{ waitingSplit }}</p>
                <component
                  :is="docTo(w.oldest[0]) ? 'router-link' : 'div'"
                  v-if="w.oldest.length"
                  :to="docTo(w.oldest[0])"
                  class="mt-3 flex min-h-secondary items-center justify-between gap-3 rounded-xl bg-surface-page px-3 py-2 active:bg-brand-soft"
                >
                  <span class="min-w-0">
                    <span class="block text-[13px] text-ink-faint">Longest waiting</span>
                    <span class="block truncate text-[15px] font-semibold">{{ w.oldest[0].name }}</span>
                    <span class="block truncate text-[13px] text-ink-faint">{{ w.oldest[0].party }}</span>
                  </span>
                  <span class="tnum shrink-0 text-[15px] font-bold text-warn-text">{{ ageText(w.oldest[0].hours) }}</span>
                </component>
                <button v-if="w.oldest.length > 1" type="button" :class="LINK" @click="openWaiting">Oldest {{ w.oldest.length }} ›</button>
              </template>
            </template>
          </StatCard>

          <StatCard v-if="show('stuck')" title="Stuck with nobody" scope="In no one's approval queue · live" :state="st('stuck')" :error-detail="SECTION_ERR" @retry="reload">
            <template v-if="d && d.stuck">
              <p v-if="d.stuck.partial" class="mb-3 rounded-lg bg-warn-bg px-3 py-2 text-[13px] text-warn-text">Part of this couldn't be read, so the count may be low.</p>
              <div v-if="!d.stuck.count && !d.stuck.partial" class="py-2 text-[17px] font-bold">Nothing is stuck</div>
              <template v-else-if="d.stuck.count">
                <div class="tnum text-[38px] font-bold leading-none text-warn-text">{{ d.stuck.count }}</div>
                <button type="button" :class="LINK" @click="openStuck">See what is stuck ›</button>
              </template>
            </template>
          </StatCard>

          <StatCard v-if="show('approved')" title="Approved by me" :scope="`${scope} · all companies`" :state="st('approved')" :error-detail="SECTION_ERR" @retry="reload">
            <template v-if="d && d.approved">
              <div class="tnum text-[38px] font-bold leading-none">{{ d.approved.count }}</div>
              <p class="tnum mt-2 text-[14px] text-ink-muted">{{ d.approved.count === 1 ? "purchase order" : "purchase orders" }} worth {{ moneyCompact(d.approved.value) }}</p>
            </template>
          </StatCard>

          <StatCard v-if="show('purchases')" title="Purchases" :scope="`Approved POs · ${company} · ${scope}`" :state="st('purchases')" :error-detail="SECTION_ERR" @retry="reload">
            <template v-if="p">
              <div class="tnum text-[38px] font-bold leading-none">{{ moneyCompact(p.current) }}</div>
              <p class="tnum mt-2 text-[14px] text-ink-muted" :aria-label="deltaText(p.current, p.previous, true) + ' · ' + plural(p.count, 'PO')">
                {{ deltaText(p.current, p.previous) }} · {{ plural(p.count, "PO") }}
              </p>
              <div v-for="c in p.by_category" :key="c.category" class="mt-2.5">
                <div class="tnum flex justify-between gap-3 text-[14px]"><span>{{ c.category }}</span><span class="font-bold">{{ moneyCompact(c.value) }}</span></div>
                <div class="mt-1 h-2 rounded bg-surface-page" aria-hidden="true">
                  <div class="h-2 rounded" :style="{ width: share(c.value, p.current) + '%', background: 'var(--exec-brand)' }"></div>
                </div>
              </div>
              <!-- Bars are CSS (no chart library); the sheet carries the values. -->
              <button v-if="p.trend.length" type="button" class="mt-4 block w-full text-left" aria-label="Last 6 months, show the monthly figures" @click="openTrend">
                <span class="block text-[13px] text-ink-faint">Last 6 months ›</span>
                <span class="mt-1 flex h-16 items-end gap-2" aria-hidden="true">
                  <span
                    v-for="(m, i) in p.trend"
                    :key="m.label"
                    class="flex-1 rounded-t"
                    :style="{ height: Math.max(share(m.total, trendMax), 3) + '%', background: i === p.trend.length - 1 ? 'var(--exec-brand)' : 'var(--exec-ink-faint)' }"
                  ></span>
                </span>
                <span class="mt-1 grid grid-cols-6 gap-2 text-center text-[13px] text-ink-faint" aria-hidden="true">
                  <span v-for="m in p.trend" :key="m.label">{{ m.label }}</span>
                </span>
              </button>
              <button v-if="p.top_suppliers.length" type="button" :class="LINK" @click="openSuppliers">Top suppliers ›</button>
            </template>
          </StatCard>

          <StatCard v-if="show('budget')" title="Budget vs actual" :scope="budgetScope" :state="st('budget')" :error-detail="SECTION_ERR" @retry="reload">
            <template v-if="b">
              <p v-if="b.state === 'restricted'" class="text-[14px] text-ink-muted">Not shown for your role.</p>
              <p v-else-if="!b.total_rows" class="text-[14px] text-ink-muted">No submitted budgets for this fiscal year.</p>
              <template v-else>
                <div class="tnum text-[38px] font-bold leading-none" :class="over(b.summary.overall_utilization) ? 'text-danger-text' : ''">{{ pctText(b.summary.overall_utilization) }}</div>
                <div class="mt-2.5 h-2.5 rounded-full bg-surface-page" aria-hidden="true">
                  <div class="h-2.5 rounded-full" :style="barStyle(b.summary.overall_utilization)"></div>
                </div>
                <p class="tnum mt-2 text-[14px] text-ink-muted">{{ moneyCompact(budgetUsed) }} of {{ moneyCompact(b.summary.total_budget) }} spent or committed</p>
                <button type="button" :class="[LINK, b.over_count ? '!text-danger-text' : '']" @click="openBudget">
                  {{ b.over_count ? `${plural(b.over_count, "cost centre")} over budget ›` : "By cost centre ›" }}
                </button>
              </template>
            </template>
          </StatCard>

          <StatCard v-if="show('grain')" title="Grain received" :scope="`Net weight · ${scope} · all companies`" :state="st('grain')" :error-detail="SECTION_ERR" @retry="reload">
            <template v-if="d && d.grain">
              <div class="tnum text-[38px] font-bold leading-none">{{ tonnes(d.grain.mt) }} MT</div>
              <p class="tnum mt-2 text-[14px] text-ink-muted" :aria-label="plural(d.grain.loads, 'load') + ' · ' + deltaText(d.grain.mt, d.grain.prev_mt, true)">
                {{ plural(d.grain.loads, "load") }} · {{ deltaText(d.grain.mt, d.grain.prev_mt) }}
              </p>
            </template>
          </StatCard>

          <StatCard v-if="show('companies')" title="Companies" :scope="`Each company's own documents · ${scope}`" :state="st('companies')" :error-detail="SECTION_ERR" @retry="reload">
            <div v-for="c in (d && d.companies && d.companies.rows) || []" :key="c.company" class="border-b border-surface-line py-3 first:pt-0 last:border-0 last:pb-0">
              <div class="flex justify-between gap-3 text-[15px] font-bold"><span class="truncate">{{ c.abbr }}</span><span class="tnum shrink-0">{{ moneyCompact(c.po_value) }}</span></div>
              <p class="tnum mt-0.5 text-[13px] text-ink-muted">{{ plural(c.po_count, "PO") }} · {{ plural(c.pr_count, "receipt") }} · bills {{ moneyCompact(c.pi_value) }}</p>
              <p class="tnum text-[13px]" :class="c.budget_state === 'ok' && over(c.budget_pct) ? 'text-danger-text' : 'text-ink-muted'">{{ companyBudget(c) }}</p>
            </div>
          </StatCard>

          <StatCard v-if="show('plant')" title="Gate & receiving" scope="Live" :state="st('plant')" :error-detail="SECTION_ERR" @retry="reload">
            <div v-if="d && d.plant" class="grid grid-cols-3 gap-2">
              <div class="rounded-xl bg-surface-page p-2.5">
                <div class="tnum text-[24px] font-bold">{{ num(d.plant.arrived_24h) }}</div>
                <div class="text-[13px] text-ink-muted">Arrived in last 24&nbsp;h</div>
              </div>
              <div class="rounded-xl bg-surface-page p-2.5">
                <div class="tnum text-[24px] font-bold">{{ num(d.plant.grain_waiting) }}</div>
                <div class="text-[13px] text-ink-muted">Grain awaiting PO link</div>
                <div v-if="d.plant.grain_over_6h" class="text-[13px] font-semibold text-warn-text">{{ d.plant.grain_over_6h }} over 6&nbsp;h</div>
              </div>
              <div class="rounded-xl bg-surface-page p-2.5">
                <div class="tnum text-[24px] font-bold" :class="d.plant.receipt_owed ? 'text-danger-text' : ''">{{ num(d.plant.receipt_owed) }}</div>
                <div class="text-[13px] text-ink-muted">Left owing a receipt</div>
              </div>
            </div>
          </StatCard>

          <StatCard v-if="show('stock')" title="Stock" scope="Products · as of now · each item in its own unit" :state="stockState" :error-detail="stockState === 'unconfigured' ? STOCK_UNSET : SECTION_ERR" @retry="reload">
            <div v-for="i in (d && d.stock && d.stock.products && d.stock.products.items) || []" :key="i.item_code" class="tnum mt-1.5 flex justify-between gap-3 text-[14px] first:mt-0">
              <span class="min-w-0 truncate">{{ i.item_name }}</span><span class="shrink-0 font-bold">{{ num(i.qty) }} {{ i.uom }}</span>
            </div>
          </StatCard>

          <StatCard v-if="show('brandless')" title="Non-branded items" scope="Enabled items with no brand set" :state="st('brandless')" :error-detail="SECTION_ERR" @retry="reload">
            <template v-if="d && d.brandless">
              <div class="tnum text-[38px] font-bold leading-none">{{ num(d.brandless.count) }}</div>
              <button v-if="d.brandless.items.length" type="button" :class="LINK" @click="openBrandless">Most bought {{ d.brandless.items.length }} ›</button>
            </template>
          </StatCard>
        </template>
      </div>
    </PullToRefresh>

    <StatSheet :open="sheet.open" :title="sheet.title" :subtitle="sheet.subtitle" :footnote="sheet.footnote" :rows="sheet.rows" @close="sheet.open = false" />
    <TabBar />
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref } from "vue"

import AlertsSheet from "@/components/AlertsSheet.vue"
import AppHeader from "@/components/AppHeader.vue"
import PullToRefresh from "@/components/PullToRefresh.vue"
import StatCard from "@/components/StatCard.vue"
import StatSheet from "@/components/StatSheet.vue"
import TabBar from "@/components/TabBar.vue"
import { DASHBOARD_VIEW, PERIODS, dashboard, isStale, loadDashboard } from "@/data/dashboard.js"
import { DOCTYPE_META, moneyCompact } from "@/data/format.js"

const SECTION_ERR = "This section could not load."
const STOCK_UNSET = "The product item list is not configured (TS Settings → Ethanol items)."
const LINK = "mt-2 min-h-secondary text-[14px] font-bold text-brand-deep"
// Card order per view (1 = CEO, 2 = MD), same sections as the desk pages.
const SECTIONS = {
  1: ["waiting", "stuck", "purchases", "budget", "plant", "stock"],
  2: ["waiting", "approved", "purchases", "budget", "grain", "companies", "plant", "brandless"],
}

const sheet = reactive({ open: false, title: "", subtitle: "", footnote: "", rows: [] })
const tick = ref(0) // re-evaluates the time-based "stale" flag

const d = computed(() => dashboard.data)
const w = computed(() => d.value && d.value.waiting)
const p = computed(() => d.value && d.value.purchases)
const b = computed(() => d.value && d.value.budget)
const stale = computed(() => (tick.value, isStale()))

const show = (key) => (SECTIONS[DASHBOARD_VIEW] || []).includes(key)
const periodOf = (key) => PERIODS.find((x) => x.key === key) || PERIODS[2]
// Labels follow the figures on screen (the payload's period), never the chip just tapped.
const scope = computed(() => (d.value ? periodOf(d.value.filters.date_range).scope : ""))
const company = computed(() => (d.value ? d.value.filters.company : ""))
const prevRange = computed(() => (d.value ? dateRange(d.value.filters.prev_start, d.value.filters.prev_end) : ""))
const asOfTime = computed(() => (d.value ? String(d.value.as_of).slice(11, 16) : ""))
const asOfLine = computed(() => {
  if (!d.value) return " "
  const f = d.value.filters
  const base = `${periodOf(f.date_range).label} · ${dateRange(f.start, f.end)} · as of ${asOfTime.value}`
  return stale.value && !dashboard.error ? `${base} · pull down to refresh` : base
})

const waitingScope = DASHBOARD_VIEW === 2 ? "At the MD step · live" : "At the CEO step · live"
const waitingSplit = computed(() =>
  [["pos", "PO", "POs"], ["mrs", "MR", "MRs"], ["overrides", "budget override", "budget overrides"], ["post_dated", "post-dated", "post-dated"]]
    .filter(([k]) => w.value[k] && w.value[k].count)
    .map(([k, one, many]) => `${w.value[k].count} ${w.value[k].count === 1 ? one : many}`)
    .join(" · ")
)
const trendMax = computed(() => Math.max(0, ...p.value.trend.map((m) => m.total)))
const budgetScope = computed(() => (b.value && b.value.fiscal_year ? `FY ${b.value.fiscal_year} · ${company.value}` : company.value))
const budgetUsed = computed(() => (b.value.summary.total_committed || 0) + (b.value.summary.total_actual || 0))
const stockState = computed(() => {
  const s = st("stock")
  return s === "ok" && d.value.stock.products.state === "unconfigured" ? "unconfigured" : s
})

function st(key) {
  if (!d.value) return "loading"
  const s = d.value[key]
  return s && s.state !== "error" ? "ok" : "error"
}

const docTo = (r) => (r && DOCTYPE_META[r.doctype] ? { name: "document", params: { doctype: r.doctype, name: r.name } } : null)
const num = (v) => (v === null || v === undefined ? "—" : Number(v).toLocaleString("en-IN"))
const plural = (n, word) => `${num(n)} ${n === 1 ? word : word + "s"}`
const tonnes = (v) => Number(v || 0).toLocaleString("en-IN", { minimumFractionDigits: 1, maximumFractionDigits: 1 })
const ageText = (h) => (h < 24 ? `${Math.round(h)} h` : `${Math.round(h / 24)} d`)
const share = (v, total) => (total > 0 ? Math.min(100, (v / total) * 100) : 0)
const over = (pct) => Number(pct) > 100
const pctText = (pct) => (Number(pct) > 999 ? ">999%" : `${Math.round(Number(pct || 0) * 10) / 10}%`)
const barStyle = (pct) => ({ width: Math.min(Math.max(Number(pct) || 0, 0), 100) + "%", background: over(pct) ? "var(--exec-danger-text)" : "var(--exec-brand)" })

// Spend going up is neither good nor bad, so a delta is never green or red.
// words=true gives the screen-reader form ("up 12%"), not the glyph.
function deltaText(cur, prev, words = false) {
  if (!prev) return `nothing in ${prevRange.value} to compare`
  const pct = Math.abs(((cur - prev) / prev) * 100)
  const dir = cur >= prev ? (words ? "up" : "▲") : words ? "down" : "▼"
  return `${dir} ${pct > 999 ? ">999" : Math.round(pct)}% vs ${prevRange.value}`
}
function companyBudget(c) {
  if (c.budget_state === "restricted") return "Budget not shown for your role"
  if (c.budget_state !== "ok") return "Budget could not load"
  if (!c.budget_total) return "No budget this fiscal year"
  return `Budget ${pctText(c.budget_pct)} used` + (over(c.budget_pct) ? " · over budget" : "")
}
// "1–18 Oct", "28 Sep – 4 Oct", "1 Apr 2026 – 18 Feb", "1 Apr – 18 Oct 2025".
// The end date carries its year unless it is this year — otherwise last
// year's comparison range reads exactly like this year's.
function dateRange(a, z) {
  if (!a) return ""
  const A = new Date(a + "T00:00:00")
  const Z = new Date(z + "T00:00:00")
  const dm = { day: "numeric", month: "short" }
  const dmy = { ...dm, year: "numeric" }
  const zo = Z.getFullYear() === new Date().getFullYear() ? dm : dmy
  const fmt = (dt, o) => dt.toLocaleDateString("en-IN", o)
  if (a === z) return fmt(Z, zo)
  if (A.getFullYear() !== Z.getFullYear()) return `${fmt(A, dmy)} – ${fmt(Z, zo)}`
  return A.getMonth() === Z.getMonth() ? `${A.getDate()}–${fmt(Z, zo)}` : `${fmt(A, dm)} – ${fmt(Z, zo)}`
}

function openSheet(title, subtitle, rows, footnote = "") {
  Object.assign(sheet, { open: true, title, subtitle, rows, footnote })
}
const openWaiting = () =>
  openSheet("Waiting for me", "Longest waiting first", w.value.oldest.map((r) => ({
    label: r.name,
    sub: [r.kind, r.party, r.value ? moneyCompact(r.value) : ""].filter(Boolean).join(" · "),
    value: ageText(r.hours),
    tone: r.hours >= 24 ? "warn" : "",
    to: docTo(r),
  })))
const openStuck = () =>
  openSheet("Stuck with nobody", "Open these on the desk to route them", d.value.stuck.items.map((i) => ({ label: i.text, value: `${Math.round(i.days)} d`, tone: "warn", wrap: true })))
const openTrend = () =>
  openSheet("Last 6 months", `Approved PO value · ${company.value}`, p.value.trend.map((m) => ({ label: m.label, value: moneyCompact(m.total) })))
const openSuppliers = () =>
  openSheet("Top suppliers", `By approved PO value · ${scope.value}`, p.value.top_suppliers.map((s) => ({
    // No category: the payload gives one per supplier (its largest PO's), and
    // most suppliers span several — "Grain · 132 POs" would be wrong.
    label: s.supplier_name || s.supplier, sub: plural(s.count, "PO"), value: moneyCompact(s.value),
  })))
const openBrandless = () =>
  openSheet("Non-branded items", "Most bought first", d.value.brandless.items.map((i) => ({ label: i.item_name, sub: i.item_group, value: plural(i.po_count, "PO") })))
function openBudget() {
  const rows = b.value.rows.map((r) => ({
    label: r.label,
    sub: `${moneyCompact(r.used)} of ${moneyCompact(r.budget)}` + (over(r.pct) ? " · over budget" : ""),
    value: pctText(r.pct),
    tone: over(r.pct) ? "danger" : "",
  }))
  const more = b.value.total_rows > rows.length ? `Showing ${rows.length} of ${b.value.total_rows}.` : ""
  openSheet("Budget by cost centre", `FY ${b.value.fiscal_year} · spent + committed`, rows, more)
}

async function reload() {
  await loadDashboard()
  tick.value++
}

let staleTimer = null
// An installed PWA is usually RESUMED, not launched. Matches Overview.
function onVisible() {
  if (document.visibilityState === "visible") reload()
}
onMounted(() => {
  reload()
  document.addEventListener("visibilitychange", onVisible)
  staleTimer = setInterval(() => (tick.value += 1), 30000)
})
onUnmounted(() => {
  document.removeEventListener("visibilitychange", onVisible)
  if (staleTimer) clearInterval(staleTimer)
  staleTimer = null
})
</script>
