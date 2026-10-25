import { Outlet } from 'react-router-dom'
import { Sidebar } from './Sidebar'
import { Breadcrumbs } from './Breadcrumbs'
import { ThemeToggle } from './ThemeToggle'
import { Toaster } from '@/components/ui/Toast'

/** 应用外壳：左侧边栏 + 顶栏（面包屑、主题切换）+ 内容区 */
export function AppLayout() {
  return (
    <div className="flex h-full bg-background text-foreground">
      <Sidebar />
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-12 shrink-0 items-center justify-between gap-4 border-b border-border px-5">
          <Breadcrumbs />
          <ThemeToggle />
        </header>
        <main className="min-h-0 flex-1 overflow-y-auto">
          <Outlet />
        </main>
      </div>
      <Toaster />
    </div>
  )
}
