import type { Config } from 'tailwindcss'

// 颜色通过 CSS 变量定义（见 src/index.css），亮/暗主题切换只需改变量
const v = (name: string) => `hsl(var(--${name}) / <alpha-value>)`

export default {
  darkMode: ['class'],
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        background: v('background'),
        foreground: v('foreground'),
        card: v('card'),
        muted: { DEFAULT: v('muted'), foreground: v('muted-foreground') },
        border: v('border'),
        input: v('input'),
        ring: v('ring'),
        primary: { DEFAULT: v('primary'), foreground: v('primary-foreground') },
        secondary: { DEFAULT: v('secondary'), foreground: v('secondary-foreground') },
        accent: { DEFAULT: v('accent'), foreground: v('accent-foreground') },
        success: { DEFAULT: v('success'), foreground: v('success-foreground') },
        warning: { DEFAULT: v('warning'), foreground: v('warning-foreground') },
        danger: { DEFAULT: v('danger'), foreground: v('danger-foreground') },
        sidebar: { DEFAULT: v('sidebar'), foreground: v('sidebar-foreground') },
      },
      fontFamily: {
        sans: ['Inter', 'ui-sans-serif', 'system-ui', '-apple-system', 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', 'sans-serif'],
        mono: ['ui-monospace', 'SFMono-Regular', 'JetBrains Mono', 'Menlo', 'Consolas', 'monospace'],
      },
      borderRadius: { lg: '0.5rem', md: '0.375rem', sm: '0.25rem' },
    },
  },
  plugins: [],
} satisfies Config
