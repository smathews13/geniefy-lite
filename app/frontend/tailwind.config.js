/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        // D23 confidence/readiness scale (perceptually ordered cool-grey → amber → green).
        // Always paired with an icon/label in components — never color alone (D23 P3).
        confidence: {
          low: '#64748b',
          mid: '#f59e0b',
          high: '#16a34a',
        },
      },
    },
  },
  plugins: [],
}
