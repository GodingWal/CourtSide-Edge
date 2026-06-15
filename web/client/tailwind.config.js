/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        'cs-black': '#0a0a0a',
        'cs-dark': '#111111',
        'cs-card': '#161616',
        'cs-border': '#222222',
        'cs-muted': '#555555',
        'cs-red': '#dc2626',
        'cs-red-bright': '#ef4444',
        'cs-red-glow': 'rgba(220, 38, 38, 0.15)',
        /* Severity colors for state indication */
        'cs-success': '#22c55e',
        'cs-success-dim': 'rgba(34, 197, 94, 0.12)',
        'cs-warning': '#eab308',
        'cs-warning-dim': 'rgba(234, 179, 8, 0.12)',
        'cs-danger': '#ef4444',
        'cs-danger-dim': 'rgba(239, 68, 68, 0.12)',
        'cs-info': '#3b82f6',
        'cs-info-dim': 'rgba(59, 130, 246, 0.12)',
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
        mono: ['JetBrains Mono', 'Fira Code', 'monospace'],
      },
      boxShadow: {
        'glow-red': '0 0 20px rgba(220, 38, 38, 0.15), 0 0 60px rgba(220, 38, 38, 0.05)',
        'glow-red-sm': '0 0 10px rgba(220, 38, 38, 0.1)',
        'glow-success': '0 0 12px rgba(34, 197, 94, 0.15)',
        'glow-warning': '0 0 12px rgba(234, 179, 8, 0.15)',
        'glow-danger': '0 0 16px rgba(239, 68, 68, 0.25)',
        'glow-info': '0 0 12px rgba(59, 130, 246, 0.15)',
        'card': '0 4px 30px rgba(0, 0, 0, 0.3)',
      },
      animation: {
        'pulse-slow': 'pulse 3s cubic-bezier(0.4, 0, 0.6, 1) infinite',
        'pulse-danger': 'pulseDanger 1.5s cubic-bezier(0.4, 0, 0.6, 1) infinite',
        'fade-in': 'fadeIn 0.5s ease-out',
        'slide-up': 'slideUp 0.4s ease-out',
        'shimmer': 'shimmer 2s ease-in-out infinite',
        'bar-fill': 'barFill 0.8s ease-out forwards',
      },
      keyframes: {
        fadeIn: {
          '0%': { opacity: '0' },
          '100%': { opacity: '1' },
        },
        slideUp: {
          '0%': { opacity: '0', transform: 'translateY(10px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        shimmer: {
          '0%, 100%': { opacity: '0.4' },
          '50%': { opacity: '1' },
        },
        barFill: {
          '0%': { width: '0%' },
          '100%': { width: 'var(--bar-width)' },
        },
        pulseDanger: {
          '0%, 100%': { opacity: '1', boxShadow: '0 0 0 0 rgba(239, 68, 68, 0.4)' },
          '50%': { opacity: '0.85', boxShadow: '0 0 0 8px rgba(239, 68, 68, 0)' },
        },
      },
    },
  },
  plugins: [],
}
