/** @type {import('tailwindcss').Config} */
export default {
  darkMode: "class",
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // driven by CSS vars in index.css so light/dark swap cleanly
        app: "var(--bg-app)",
        sidebar: "var(--bg-sidebar)",
        surface: "var(--bg-surface)",
        hover: "var(--bg-hover)",
        line: "var(--border)",
        "text-primary": "var(--text-primary)",
        "text-secondary": "var(--text-secondary)",
        "text-disabled": "var(--text-disabled)",
        lava: "#FF3621",
        "lava-warm": "#FF5F46",
        brand: "var(--brand-blue)",
        "brand-hover": "var(--brand-blue-hover)",
        critical: "#E5484D",
        warning: "#FFAB00",
        info: "#4C8DFF",
        neutral: "#6B7482",
        success: "#3DD68C",
      },
      fontFamily: {
        sans: ['"DM Sans"', "system-ui", "-apple-system", "Segoe UI", "sans-serif"],
      },
      borderRadius: {
        xl: "12px",
      },
      boxShadow: {
        card: "var(--shadow-card)",
        pop: "0 12px 32px rgba(0,0,0,0.35)",
      },
    },
  },
  plugins: [],
};
