<template>
  <div class="pb-28">
    <AppHeader title="Usage" bell refreshable :refreshing="usage.loading" @refresh="reload" />
    <AlertsSheet />

    <PullToRefresh :refreshing="usage.loading" @refresh="reload">
      <div class="flex flex-col gap-3 px-4">
        <!-- Period chips. The client sends days ∈ {7,30,90}, never dates —
             every server-side date-validation throw is structurally
             unreachable, including "To Date cannot be in the future" from a
             phone clock running ahead of the server. -->
        <div class="flex flex-wrap gap-1.5 pb-2">
          <button
            v-for="w in WINDOWS"
            :key="w"
            type="button"
            class="min-h-[48px] rounded-full border px-3 text-[13px] font-semibold"
            :class="usage.days === w ? 'border-brand-deep bg-brand-deep text-white' : 'border-surface-line bg-surface text-ink-muted'"
            @click="setDays(w)"
          >
            {{ w }} days
          </button>
        </div>

        <p class="text-[13px]" :class="stale && !usage.error ? 'text-warn-text' : 'text-ink-faint'">
          {{ asOfLine }}
        </p>
        <p v-if="usage.days === 90" class="-mt-2 text-[13px] text-ink-faint">
          90 days is the maximum — sign-in history is deleted after that.
        </p>

        <!-- A refresh that FAILED while older data is on screen must say so. -->
        <div
          v-if="usage.error && usage.data"
          class="rounded-xl bg-warn-bg px-3 py-2 text-[13px] text-warn-text"
          role="status"
        >
          Couldn't refresh just now — showing earlier figures. {{ usage.error.display() }}
        </div>

        <div
          v-if="d && d.partial"
          class="rounded-xl bg-warn-bg px-3 py-2 text-[13px] text-warn-text"
          role="status"
        >
          Some figures are incomplete — {{ d.failed_source_count }} data
          source{{ d.failed_source_count === 1 ? "" : "s" }} didn't respond.
          Affected numbers show "n/a", never 0.
        </div>

        <!-- ── Needs a look ──────────────────────────────────────────────── -->
        <StatCard
          v-if="alertRows.length || peopleAlert"
          title="Needs a look"
          scope="Different problems, told apart on sight."
          :state="cardState"
          :error-detail="errDetail"
          @retry="reload"
        >
          <template v-if="d">
            <template v-if="usingRows.length">
              <p class="text-[13px] font-bold uppercase tracking-wide text-ink-faint">
                Not using the system — ask them
              </p>
              <div
                v-for="r in usingRows"
                :key="r.dept"
                class="border-b border-surface-line py-2 last:border-b-0"
              >
                <div class="flex items-center justify-between gap-3">
                  <span class="min-w-0 truncate text-[15px] font-semibold text-warn-text">{{ r.dept }}</span>
                  <span class="tnum text-[17px] font-bold text-warn-text">0</span>
                </div>
                <p class="mt-0.5 flex items-center gap-1.5 text-[13px]" :class="r.state === 'absent' ? 'text-danger-text' : 'text-warn-text'">
                  <span class="dot pulse" :class="r.state === 'absent' ? 'dot-absent' : 'dot-idle'" aria-hidden="true"></span>
                  <template v-if="r.state === 'absent'">Nobody signed in for {{ d.range.days }} days</template>
                  <template v-else>Signed in, but created and approved nothing for {{ d.range.days }} days</template>
                </p>
                <p v-if="priorLine(r.dept)" class="mt-0.5 text-[13px] text-ink-faint">
                  {{ priorLine(r.dept) }}
                </p>
              </div>

              <div v-if="peopleAlert" class="border-b border-surface-line py-2 last:border-b-0">
                <div class="flex items-center justify-between gap-3">
                  <span class="text-[15px] font-semibold text-danger-text">People not signing in</span>
                  <span class="tnum text-[17px] font-bold text-danger-text">{{ d.people.inactive_over_30d + d.people.never_signed_in }}</span>
                </div>
                <p class="mt-0.5 flex items-center gap-1.5 text-[13px] text-danger-text">
                  <span class="dot dot-absent pulse" aria-hidden="true"></span>
                  No sign-in for 30 days — {{ d.people.never_signed_in }} of them never have
                </p>
              </div>
            </template>

            <template v-if="blindRows.length">
              <p class="mt-3 text-[13px] font-bold uppercase tracking-wide text-ink-faint">
                Working, but we don't count it — fix the screen
              </p>
              <div v-for="r in blindRows" :key="r.dept" class="py-2">
                <div class="flex items-center justify-between gap-3">
                  <span class="min-w-0 truncate text-[15px] font-semibold text-gap-text">{{ r.dept }}</span>
                </div>
                <p class="mt-0.5 flex items-center gap-1.5 text-[13px] text-gap-text">
                  <span class="dot dot-blind pulse" aria-hidden="true"></span>
                  Nothing they do is among the document types this screen counts
                </p>
                <p class="mt-0.5 text-[13px] text-ink-faint">
                  {{ r.signed_in }} of {{ r.users }} signed in · their 0 is our gap, not theirs
                </p>
              </div>
            </template>

            <!-- Legend: colour is never the only difference — ring / dot /
                 square, and each row states its condition in words. -->
            <div class="mt-2 flex flex-col gap-1.5 border-t border-surface-line pt-2">
              <p class="flex items-center gap-2 text-[13px] text-ink-muted">
                <span class="dot dot-idle" aria-hidden="true"></span>
                <span><b class="text-ink">Signs in, records nothing.</b> Opens the system, creates and approves no documents.</span>
              </p>
              <p class="flex items-center gap-2 text-[13px] text-ink-muted">
                <span class="dot dot-absent" aria-hidden="true"></span>
                <span><b class="text-ink">Does not sign in.</b> Has not opened the system at all.</span>
              </p>
              <p class="flex items-center gap-2 text-[13px] text-ink-muted">
                <span class="dot dot-blind" aria-hidden="true"></span>
                <span><b class="text-ink">Works in documents this screen doesn't count.</b> Their 0 is our gap, not theirs.</span>
              </p>
            </div>
          </template>
        </StatCard>

        <!-- ── Hero ──────────────────────────────────────────────────────── -->
        <StatCard
          title="Recorded activity"
          scope="Company-wide. Counts of recorded actions — not hours worked."
          :state="cardState"
          :error-detail="errDetail"
          @retry="reload"
        >
          <template v-if="t">
            <div v-if="!t.actions && !d.partial" class="py-2">
              <div class="text-[17px] font-bold">Nothing recorded in this window</div>
              <p class="mt-1 text-[14px] text-ink-muted">
                No document was created and no approval was decided.
              </p>
            </div>
            <template v-else>
              <div class="flex items-end gap-2">
                <span class="tnum text-[38px] font-bold leading-none">{{ num(t.actions) }}<sup v-if="d.partial" class="text-[20px]">±</sup></span>
                <span class="min-w-0 pb-1 text-[14px] text-ink-muted">documents created and approvals decided</span>
              </div>
              <p class="mt-2 text-[15px]"><b>{{ t.active_users }}</b> of {{ t.total_users }} people used the system</p>
              <p class="mt-1 text-[13px] text-ink-faint">
                {{ t.logins === null ? "sign-ins n/a" : num(t.logins) + " sign-ins" }}
              </p>
            </template>
          </template>
        </StatCard>

        <!-- ── By department ─────────────────────────────────────────────── -->
        <StatCard
          title="By department"
          scope="Departments are worked out from each person's ERP roles — the system holds no HR department record. One person can hold roles in several departments; this list counts each person once, the folded-teams view counts them again per role."
          :state="cardState"
          :error-detail="errDetail"
          @retry="reload"
        >
          <template v-if="d">
            <div class="flex flex-col">
              <button
                v-for="r in deptRows"
                :key="r.dept"
                type="button"
                class="border-b border-surface-line py-2 text-left last:border-b-0"
                @click="openDept(r)"
              >
                <div class="flex min-w-0 items-center justify-between gap-3">
                  <span
                    class="min-w-0 truncate text-[15px] font-semibold"
                    :class="r.state === 'idle' ? 'text-warn-text' : r.state === 'absent' ? 'text-danger-text' : r.state === 'blind' ? 'text-gap-text' : 'text-ink'"
                  >{{ r.dept }}</span>
                  <span class="flex shrink-0 items-center gap-1">
                    <span class="tnum text-[17px]" :class="r.actions ? 'font-bold text-ink' : 'font-semibold text-ink-faint'">{{ num(r.actions) }}</span>
                    <span class="text-[17px] text-ink-faint" aria-hidden="true">›</span>
                  </span>
                </div>
                <p class="mt-0.5 flex items-center gap-1.5 text-[13px]" :class="subToneClass(r)">
                  <span v-if="r.state !== 'ok'" class="dot pulse" :class="'dot-' + r.state" aria-hidden="true"></span>
                  {{ subLine(r) }}
                </p>
                <div v-if="r.actions" class="mt-1.5 h-1 overflow-hidden rounded-full bg-surface-page" aria-hidden="true">
                  <div class="h-full rounded-full bg-brand" :style="{ width: barPct(r) + '%', minWidth: '3px' }"></div>
                </div>
              </button>

              <button
                v-if="d.folded_teams && d.folded_teams.length"
                type="button"
                class="border-t border-surface-line py-2 text-left"
                @click="foldedOpen = true"
              >
                <div class="flex items-center justify-between gap-3">
                  <span class="text-[15px] font-semibold text-ink-muted">Where are IT, Production, Asset &amp; Returns?</span>
                  <span class="text-[17px] text-ink-faint" aria-hidden="true">›</span>
                </div>
                <p class="mt-0.5 text-[13px] text-ink-faint">
                  Folded into Executive and Department Head by role ranking — tap to see them
                </p>
              </button>
            </div>
          </template>
        </StatCard>

        <!-- ── What these numbers don't say ──────────────────────────────── -->
        <StatCard title="What these numbers don't say" scope="">
          <div class="flex flex-col gap-2">
            <p class="text-[13px] text-ink-muted"><b class="text-ink">Accounting work is not counted.</b> Purchase Invoices, Payment Entries and Journal Entries are outside the document types this screen measures — Accounts reads 0 here even when the team is busy.</p>
            <p class="text-[13px] text-ink-muted"><b class="text-ink">Gate, weighbridge and stores use shared shift logins.</b> A team's total is a post's total, not one person's.</p>
            <p class="text-[13px] text-ink-muted"><b class="text-ink">Departments come from ERP roles, first match wins.</b> Anyone holding a director role is counted under Executive whatever work they do, and "Stock" is where people whose roles match nothing else end up.</p>
            <p class="text-[13px] text-ink-muted"><b class="text-ink">These are recorded actions.</b> They cannot measure hours worked, off-system work or presence, and must not be read as a performance score.</p>
          </div>
        </StatCard>
      </div>
    </PullToRefresh>

    <!-- Per-department decomposition — the mechanism that stops the row's
         single number reading as a productivity figure. -->
    <StatSheet
      :open="sheet.open"
      :title="sheet.title"
      :subtitle="sheet.subtitle"
      :footnote="sheet.footnote"
      :rows="sheet.rows"
      @close="sheet.open = false"
    />

    <!-- Folded teams: multi-membership counts. Sentences, no bars, no sums. -->
    <StatSheet
      :open="foldedOpen"
      title="Folded teams"
      subtitle="Counted again, by the roles people hold. These overlap the list and add to no total."
      footnote="Each person is counted once in the department list and again here per role. The Administrator system account is excluded throughout."
      :rows="foldedRows"
      @close="foldedOpen = false"
    />

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
import { isStale, loadUsage, usage } from "@/data/usage.js"

const WINDOWS = [7, 30, 90]

const sheet = reactive({ open: false, title: "", subtitle: "", footnote: "", rows: [] })
const foldedOpen = ref(false)
const tick = ref(0)

const d = computed(() => usage.data)
const t = computed(() => d.value && d.value.totals)
const stale = computed(() => (tick.value, isStale()))

const num = (v) => (v === null || v === undefined ? "n/a" : new Intl.NumberFormat("en-IN").format(v))

const asOfLine = computed(() => {
  if (!d.value) return " "
  const base = `${shortRange(d.value.range)} · as of ${new Date(usage.loadedAt).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" })}`
  if (usage.error) return base
  return stale.value ? `${base} · pull down to refresh` : base
})

function shortRange(r) {
  const f = new Date(r.from + "T00:00:00")
  const to = new Date(r.to + "T00:00:00")
  const fmt = (x, m) => x.toLocaleDateString("en-IN", m ? { day: "numeric", month: "short", year: "numeric" } : { day: "numeric", month: "short" })
  return `${fmt(f)} – ${fmt(to, true)}`
}

const switchedOff = computed(() =>
  Boolean(usage.error && /switched off/i.test(usage.error.display() || ""))
)
const cardState = computed(() => {
  if (usage.loading && !usage.data) return "loading"
  if (switchedOff.value && !usage.data) return "unconfigured"
  if (usage.error && !usage.data) return "error"
  return d.value ? "ok" : "error"
})
const errDetail = computed(() => (usage.error ? usage.error.display() : ""))

const deptRows = computed(() =>
  (d.value ? d.value.departments : []).map((x) => ({ ...x, actions: x.entries + x.decisions }))
)
const maxActions = computed(() => Math.max(1, ...deptRows.value.map((r) => r.actions)))
const barPct = (r) => (r.actions / maxActions.value) * 100

const alertRows = computed(() => deptRows.value.filter((r) => r.state !== "ok"))
const usingRows = computed(() => alertRows.value.filter((r) => r.state === "idle" || r.state === "absent"))
const blindRows = computed(() => alertRows.value.filter((r) => r.state === "blind"))
const peopleAlert = computed(() => {
  const p = d.value && d.value.people
  return Boolean(p && (p.inactive_over_30d || p.never_signed_in))
})

function priorLine(dept) {
  const n = d.value && d.value.prior_90d && d.value.prior_90d[dept]
  return n ? `They recorded ${num(n)} documents in the last 90 days, so this is a change, not a standing state.` : ""
}

function subToneClass(r) {
  if (r.state === "idle") return "text-warn-text"
  if (r.state === "absent") return "text-danger-text"
  if (r.state === "blind") return "text-gap-text"
  return "text-ink-faint"
}

function subLine(r) {
  if (r.state === "blind") return `${r.signed_in} of ${r.users} signed in · their work isn't among the counted document types`
  if (r.state === "absent") return `Nobody signed in for ${d.value.range.days} days`
  if (r.state === "idle") return `Signed in, but created and approved nothing for ${d.value.range.days} days`
  const logins = r.logins === null ? "sign-ins n/a" : `${num(r.logins)} sign-ins`
  return `${r.active} of ${r.users} ${r.users === 1 ? "account" : "people"} active · ${logins}`
}

function openDept(r) {
  Object.assign(sheet, {
    open: true,
    title: r.dept,
    subtitle: `${shortRange(d.value.range)} · ${r.users} ${r.users === 1 ? "person" : "people"}, ${r.active} active`,
    footnote: "Only the first two rows make up the department's number in the list. Some accounts are shared shift logins.",
    rows: [
      { label: "Documents created", value: num(r.entries) },
      { label: "Approvals decided", value: num(r.decisions) },
      { label: "Routing steps", value: num(r.flow_actions), sub: "forwarded, revised or held — not in the total" },
      { label: "Amendments", value: num(r.amendments), sub: "revised versions of earlier documents" },
      { label: "Created, later cancelled", value: num(r.since_cancelled) },
      { label: "Sign-ins", value: num(r.logins) },
    ],
  })
}

const foldedRows = computed(() => {
  const f = (d.value && d.value.folded_teams) || []
  return f.map((x) => {
    if (x.team === "Production") {
      return {
        label: "Production",
        value: x.module_off ? "module off" : `${x.active} of ${x.people} active`,
        sub: x.module_off
          ? `Production entry is switched off in ERP — there are no production documents to count. Its people's ${num(x.doc_actions)} actions are approvals and fixes already counted above${x.md_own_decisions ? `; ${num(x.md_own_decisions)} of them are the MD's own approvals` : ""}.`
          : `${num(x.doc_actions)} counted document actions · ${num(x.logins)} sign-ins.`,
      }
    }
    if (x.team === "Asset & Returns") {
      return {
        label: "Asset & Returns",
        value: `${x.active} of ${x.people} active`,
        sub: "No return-item record has ever been created, and this screen has no counter for them — unmeasured.",
      }
    }
    const alsoPrimary = x.primary_members
      ? ` ${x.primary_members} of these people also appear as the "${x.team}" row in the list.`
      : ""
    return {
      label: x.team,
      value: `${x.active} of ${x.people} active`,
      sub: `${num(x.doc_actions)} counted document actions · ${num(x.logins)} sign-ins. Mostly item masters, gate-entry fixes and system configuration — work the counts on this screen don't measure.${alsoPrimary}`,
    }
  })
})

function setDays(w) {
  if (w !== usage.days) loadUsage(w)
}

async function reload() {
  await loadUsage()
  tick.value++
}

let staleTimer = null
function onVisible() {
  // Refetch on resume only when the figures are actually stale — every
  // foreground/background flip on a phone fires this event, and each fetch
  // is two full report computations server-side.
  if (document.visibilityState === "visible" && isStale()) reload()
}

onMounted(() => {
  // TabBar's badge fetch may already have fresh data (cold-open dedupe).
  if (!usage.data || isStale()) reload()
  else tick.value++
  document.addEventListener("visibilitychange", onVisible)
  staleTimer = setInterval(() => {
    tick.value += 1
  }, 30000)
})

onUnmounted(() => {
  document.removeEventListener("visibilitychange", onVisible)
  if (staleTimer) clearInterval(staleTimer)
  staleTimer = null
})
</script>

<style scoped>
/* Three quiet states, three SHAPES — colour is never the only carrier
   (about one man in twelve cannot separate amber from red, and both readers
   are men): amber = hollow ring, red = solid dot, teal = solid square.
   Pulse: 0.63 Hz (far under the 3 Hz photosensitivity limit), six cycles,
   then holds. Off entirely under prefers-reduced-motion. */
.dot {
  width: 0.5rem;
  height: 0.5rem;
  border-radius: 999px;
  flex-shrink: 0;
  display: inline-block;
  box-sizing: border-box;
}
.dot-idle {
  background: transparent;
  border: 2px solid var(--exec-warn-text);
}
.dot-absent {
  background: var(--exec-danger-text);
}
.dot-blind {
  background: var(--exec-gap-text);
  border-radius: 2px;
}
/* Only ROW dots pulse; legend swatches stay static (approved mockup). */
.dot-idle.pulse { animation: usage-idle 1.6s ease-in-out 6; }
.dot-absent.pulse { animation: usage-absent 1.6s ease-in-out 6; }
.dot-blind.pulse { animation: usage-blind 1.6s ease-in-out 6; }
@keyframes usage-idle {
  0%, 100% { opacity: 1; box-shadow: 0 0 0 0 rgba(160, 74, 8, 0.45); }
  50% { opacity: 0.55; box-shadow: 0 0 0 5px rgba(160, 74, 8, 0); }
}
@keyframes usage-absent {
  0%, 100% { opacity: 1; box-shadow: 0 0 0 0 rgba(185, 28, 28, 0.5); }
  50% { opacity: 0.5; box-shadow: 0 0 0 5px rgba(185, 28, 28, 0); }
}
@keyframes usage-blind {
  0%, 100% { opacity: 1; box-shadow: 0 0 0 0 rgba(10, 106, 106, 0.45); }
  50% { opacity: 0.55; box-shadow: 0 0 0 5px rgba(10, 106, 106, 0); }
}
@media (prefers-reduced-motion: reduce) {
  .dot-idle.pulse, .dot-absent.pulse, .dot-blind.pulse { animation: none; }
}
</style>
