<template>
  <div class="flex min-h-dvh flex-col items-center justify-center gap-3 px-8 text-center">
    <div class="text-lg font-bold">Page not found</div>
    <router-link
      :to="{ name: 'inbox' }"
      class="min-h-secondary flex w-48 items-center justify-center rounded-xl bg-brand-deep text-[14px] font-bold text-white"
    >
      Go to Approvals
    </router-link>
  </div>
</template>
