import { createRouter, createWebHistory } from "vue-router"

import { bootSession, session } from "./data/session.js"

import DocumentDetail from "./pages/DocumentDetail.vue"
import History from "./pages/History.vue"
import Inbox from "./pages/Inbox.vue"
import Login from "./pages/Login.vue"
import NotFound from "./pages/NotFound.vue"
import Settings from "./pages/Settings.vue"

export const router = createRouter({
  history: createWebHistory("/exec/"),
  routes: [
    { path: "/login", name: "login", component: Login },
    { path: "/", name: "inbox", component: Inbox, meta: { auth: true } },
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
  return true
})
