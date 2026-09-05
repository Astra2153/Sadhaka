/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        paper:   { DEFAULT: "#FBFAF6", sunk: "#F4F1E9" },
        ink:     { DEFAULT: "#17293D", soft: "#5A6B7C" },
        rule:    { DEFAULT: "#DDD6C8", strong: "#C3B9A5" },
        credit:  "#2C6A4E",
        debit:   "#9B3A2F",
        indigo:  "#3A44A0",
        amber:   "#8A6A1F",
      },
      fontFamily: {
        serif: ["Spectral", "Georgia", "serif"],
        sans:  ["Inter", "system-ui", "sans-serif"],
      },
    },
  },
  plugins: [],
};
