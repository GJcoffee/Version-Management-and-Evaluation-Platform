import { Moon, Sun } from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { useThemeStore } from '@/stores/theme'

export function ThemeToggle() {
  const theme = useThemeStore((s) => s.theme)
  const toggle = useThemeStore((s) => s.toggle)
  const next = theme === 'dark' ? '浅色' : '深色'
  return (
    <Button variant="ghost" size="icon" onClick={toggle} aria-label={`切换到${next}主题`} title={`切换到${next}主题`}>
      {theme === 'dark' ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
    </Button>
  )
}
