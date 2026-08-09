import { createRouter, createWebHistory } from "vue-router"

import { bootSession, session } from "./data/session.js"

import DocumentDetail from "./pages/DocumentDetail.vue"
import History from "./pages/History.vue"
import Inbox from "./pages/Inbox.vue"
import Login from "./pages/Login.vue"
import NotFound from "./pages/NotFound.vue"
import Overview from "./pages/Overview.vue"
import Settings from "./pages/Settings.vue"

export const router = createRouter({
  history: createWebHistory("/exec/"),
  routes: [
    { path: "/login", name: "login", component: Login },
    { path: "/", name: "inbox", component: Inbox, meta: { auth: true } },
    {
      path: "/overview",
      name: "overview",
      component: Overview,
      meta: { auth: true, overview: true },
    },
    { path: "/history", name: "history", component: History, meta: { auth: true } },
    { path: "/settings", name: "settings", component: Settings, meta: { auth: true } },
    {
      path: "/d/:doctype/:name",
      name: "document",
      component: DocumentDetail,
      props: true,
      meta: { auth: true },
    },
    { path: "/:pathMatch(.*)*", name: "notfound", component: NotFound },
  ],
})

router.beforeEach(async (to) => {
  if (!session.booted) await bootSession()
  const guest = !session.user || session.user === "Guest"
  if (to.meta.auth && guest) return { name: "login" }
  if (to.name === "login" && !guest) return { name: "inbox" }
  // Same server-rendered flag the tab bar reads. Runs AFTER the auth guard so
  // a signed-out bookmark still lands on login, not silently on the inbox.
  // Not a security boundary — get_overview re-checks switch and audience.
  if (to.meta.overview && Number((window.execEnv || {}).overview) !== 1) {
    return { name: "inbox" }
  }
  return true
})
