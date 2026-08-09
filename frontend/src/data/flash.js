import { reactive } from "vue"

// Success-only toasts (failures render inside the action sheet, never here).
export const flash = reactive({ text: "", tone: "ok", stamp: 0 })

let timer = null

export function showFlash(text, tone = "ok") {
  flash.text = text
  flash.tone = tone
  flash.stamp = Date.now()
  if (timer) clearTimeout(timer)
  timer = setTimeout(() => {
    flash.text = ""
  }, 4500)
}
