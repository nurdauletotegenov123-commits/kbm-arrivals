/** @type {import('tailwindcss').Config} */
// Портал АО «Каражанбасмунай»: те же цвета и шрифт, что у остальных разделов сайта (static/css/app.css).
// Палитры blue/indigo заменены фирменным голубым КБМ, slate/gray — нейтральными серыми портала,
// поэтому весь интерфейс нарядов перекрашивается без правки каждого экрана.
const kbmBlue = {
  50: '#e9f5fb', 100: '#d3ebf7', 200: '#a6d6ef', 300: '#6fbde4', 400: '#2ea3d9',
  500: '#008dd1', 600: '#0071a9', 700: '#005985', 800: '#064766', 900: '#0a3650', 950: '#071f2e',
};
const kbmGray = {
  50: '#f4f6f7', 100: '#eceff0', 200: '#dbe0e3', 300: '#c2cacf', 400: '#8a969e',
  500: '#56636c', 600: '#434f57', 700: '#333d44', 800: '#232d33', 900: '#182025', 950: '#0f1519',
};

export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        blue: kbmBlue,
        indigo: kbmBlue,
        slate: kbmGray,
        gray: kbmGray,
        brand: '#0071a9',
        kbm: { blue: '#008dd1', black: '#182025', chalk: '#eceff0' },
      },
      fontFamily: {
        sans: ['"Golos Text"', 'system-ui', '-apple-system', '"Segoe UI"', 'Roboto', 'sans-serif'],
      },
      boxShadow: {
        card: '0 1px 0 rgba(24, 32, 37, .04)',
        pop: '0 8px 28px rgba(24, 32, 37, .14)',
      },
    },
  },
  plugins: [],
};
