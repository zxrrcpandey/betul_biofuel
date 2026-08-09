import { createApp } from "vue"

import App from "./App.vue"
import "./index.css"
import { router } from "./router.js"

// Service worker: scope /exec/ only — it NEVER controls /app (the desk).
// window.execEnv is injected server-side (www/exec.py) so a live phone can
// be disarmed with no deploy: sw=0 ⇒ unregister on next launch. (Named
// without dunders — frappe safe_render rejects ".__" in the template.)
const execEnv = window.execEnv || {}
if ("serviceWorker" in navigator && window.location.protocol === "https:") {
  if (Number(execEnv.sw) === 0) {
    navigator.serviceWorker.getRegistrations().then((regs) => {
      regs.forEach((r) => r.unregister())
    })
  } else if (import.meta.env.PROD) {
    window.addEventListener("load", () => {
      navigator.serviceWorker.register("/exec/sw.min.js").catch(() => {
        /* the app works fully without a SW */
      })
    })
  }
}

createApp(App).use(router).mount("#app")
