<template>
  <div
    v-if="!online"
    class="sticky top-0 z-40 bg-warn-bg px-4 py-3 text-center text-sm font-semibold text-warn-text"
  >
    You're offline. Approvals need an internet connection.
  </div>
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref } from "vue"

const online = ref(navigator.onLine)
const update = () => {
  online.value = navigator.onLine
}
onMounted(() => {
  window.addEventListener("online", update)
  window.addEventListener("offline", update)
})
onBeforeUnmount(() => {
  window.removeEventListener("online", update)
  window.removeEventListener("offline", update)
})
</script>
