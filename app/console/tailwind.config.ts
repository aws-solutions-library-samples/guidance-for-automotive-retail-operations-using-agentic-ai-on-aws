import type { Config } from "tailwindcss";

// Design tokens (spec §5.1 dealer dark, §5.9 OEM light, brand customer, neutral
// admin) are CSS variables set per theme in globals.css ([data-theme=...]); the
// Tailwind palette maps to those variables so one utility class renders the
// right colour in every app.
const v = (name: string) => `rgb(var(--${name}) / <alpha-value>)`;

const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        primary: v("c-primary"),
        "primary-light": v("c-primary-light"),
        secondary: v("c-secondary"),
        accent: v("c-accent"),
        success: v("c-success"),
        warning: v("c-warning"),
        danger: v("c-danger"),
        info: v("c-info"),
        bg: v("c-bg"),
        surface: v("c-surface"),
        fg: v("c-fg"),
        "fg-2": v("c-fg-2"),
        "fg-dim": v("c-fg-dim"),
        line: v("c-line"),
      },
      borderRadius: { sm: "var(--radius-sm)", DEFAULT: "var(--radius)", lg: "var(--radius-lg)" },
      fontFamily: {
        sans: ["-apple-system", "BlinkMacSystemFont", "Inter", "Segoe UI", "Roboto", "sans-serif"],
        mono: ["SF Mono", "Fira Code", "ui-monospace", "monospace"],
      },
      keyframes: {
        slideIn: { from: { opacity: "0", transform: "translateX(-10px)" }, to: { opacity: "1", transform: "none" } },
        pulseDot: {
          "0%,100%": { boxShadow: "0 0 0 0 rgba(16,185,129,.6)" },
          "50%": { boxShadow: "0 0 0 6px rgba(16,185,129,0)" },
        },
      },
      animation: { slideIn: "slideIn .3s ease", pulseDot: "pulseDot 2s infinite" },
    },
  },
  plugins: [],
};
export default config;
