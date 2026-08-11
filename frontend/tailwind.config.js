/** @type {import('tailwindcss').Config} */
export default {
  // No frappe-ui preset/glob: no frappe-ui components are imported in Phase 1,
  // and the preset's scan dragged ~25 dead `.dark` rules + a color-scheme:dark
  // declaration into the shipped CSS (ui-designer audit 9a). Re-add both if a
  // later phase actually imports frappe-ui components.
  content: ["./index.html", "./src/**/*.{vue,js}"],
  // Phase 3 dark mode will toggle via class; Phase 1 ships light-only with
  // every colour flowing through the CSS custom properties in tokens.css.
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        brand: {
          DEFAULT: "var(--exec-brand)",
          deep: "var(--exec-brand-deep)",
          soft: "var(--exec-brand-soft)",
        },
        ink: {
          DEFAULT: "var(--exec-ink)",
          muted: "var(--exec-ink-muted)",
          faint: "var(--exec-ink-faint)",
        },
        surface: {
          DEFAULT: "var(--exec-surface)",
          page: "var(--exec-page)",
          line: "var(--exec-line)",
        },
        // Text shades meet WCAG AA on white (700-series); the base hues are
        // reserved for backgrounds/borders only.
        ok: { text: "var(--exec-ok-text)", bg: "var(--exec-ok-bg)" },
        warn: { text: "var(--exec-warn-text)", bg: "var(--exec-warn-bg)" },
        danger: { text: "var(--exec-danger-text)", bg: "var(--exec-danger-bg)" },
        info: { text: "var(--exec-info-text)", bg: "var(--exec-info-bg)" },
        blocked: { text: "var(--exec-blocked-text)", bg: "var(--exec-blocked-bg)" },
        gap: { text: "var(--exec-gap-text)", bg: "var(--exec-gap-bg)" },
      },
      minHeight: {
        row: "88px",
        action: "56px",
        secondary: "48px",
      },
    },
  },
  plugins: [],
}
