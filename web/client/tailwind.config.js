/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        background: '#0f172a', 
        surface: 'rgba(30, 41, 59, 0.7)', 
        primary: '#3b82f6', 
        success: '#22c55e', 
        danger: '#ef4444', 
      },
      fontFamily: {
        sans: ['Inter', 'sans-serif'],
      }
    },
  },
  plugins: [],
}
