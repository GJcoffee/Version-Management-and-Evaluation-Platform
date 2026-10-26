import { lazy } from 'react'
import { createBrowserRouter, type RouteObject } from 'react-router-dom'
import { AppLayout } from '@/components/layout/AppLayout'
import type { RouteHandle } from '@/components/layout/Breadcrumbs'
import { shortenId } from '@/components/ui/IdText'
import HomePage from '@/pages/HomePage'
import AgentsPage from '@/pages/AgentsPage'
import AgentDetailPage from '@/pages/AgentDetailPage'
import EvalsPage from '@/pages/EvalsPage'
import EvalReportPage from '@/pages/EvalReportPage'
import EvalComparePage from '@/pages/EvalComparePage'
import EvalAnnotatePage from '@/pages/EvalAnnotatePage'
import SettingsPage from '@/pages/SettingsPage'
import NotFoundPage from '@/pages/NotFoundPage'

// 含编辑器 / 对话的页面按路由懒加载，缩小首屏 bundle（AppLayout 里的 Suspense 兜底）
const DraftEditorPage = lazy(() => import('@/pages/DraftEditorPage'))
const VersionDiffPage = lazy(() => import('@/pages/VersionDiffPage'))
const AssetsPage = lazy(() => import('@/pages/AssetsPage'))
const AssetDetailPage = lazy(() => import('@/pages/AssetDetailPage'))
const SessionPage = lazy(() => import('@/pages/SessionPage'))
const ComparePage = lazy(() => import('@/pages/ComparePage'))

const idCrumb = (key: string): RouteHandle['crumb'] => (p) => shortenId(p[key] ?? '')

/**
 * 路由表（详设 14.1）。handle.crumb 用于顶栏面包屑。
 * 新增页面：在 src/pages 下建文件，在这里注册。
 * 注意：后端需对非 /api、/auth 的路径回退到 index.html（SPA fallback）。
 */
export const routes: RouteObject[] = [
  {
    path: '/',
    element: <AppLayout />,
    handle: { crumb: '首页' } satisfies RouteHandle,
    children: [
      { index: true, element: <HomePage /> },
      {
        path: 'agents',
        handle: { crumb: 'Agents' } satisfies RouteHandle,
        children: [
          { index: true, element: <AgentsPage /> },
          {
            path: ':id',
            handle: { crumb: idCrumb('id') } satisfies RouteHandle,
            children: [
              { index: true, element: <AgentDetailPage /> },
              {
                path: 'draft/:draftId',
                element: <DraftEditorPage />,
                handle: { crumb: '草稿编辑器' } satisfies RouteHandle,
              },
              {
                path: 'versions/diff',
                element: <VersionDiffPage />,
                handle: { crumb: '版本对比' } satisfies RouteHandle,
              },
            ],
          },
        ],
      },
      {
        path: 'assets',
        handle: { crumb: '资产库' } satisfies RouteHandle,
        children: [
          { index: true, element: <AssetsPage /> },
          { path: ':id', element: <AssetDetailPage />, handle: { crumb: idCrumb('id') } satisfies RouteHandle },
        ],
      },
      {
        path: 'sessions/:id',
        element: <SessionPage />,
        handle: { crumb: (p) => `会话 ${shortenId(p.id ?? '')}` } satisfies RouteHandle,
      },
      {
        path: 'compare/:id',
        element: <ComparePage />,
        handle: { crumb: (p) => `对比 ${shortenId(p.id ?? '')}` } satisfies RouteHandle,
      },
      {
        path: 'evals',
        handle: { crumb: '评测' } satisfies RouteHandle,
        children: [
          { index: true, element: <EvalsPage /> },
          // 静态路径 compare 优先于 :id
          { path: 'compare', element: <EvalComparePage />, handle: { crumb: '评测对比' } satisfies RouteHandle },
          {
            path: ':id',
            handle: { crumb: idCrumb('id') } satisfies RouteHandle,
            children: [
              { index: true, element: <EvalReportPage /> },
              { path: 'annotate', element: <EvalAnnotatePage />, handle: { crumb: '人工评审' } satisfies RouteHandle },
            ],
          },
        ],
      },
      { path: 'settings', element: <SettingsPage />, handle: { crumb: '设置' } satisfies RouteHandle },
      { path: '*', element: <NotFoundPage />, handle: { crumb: '页面不存在' } satisfies RouteHandle },
    ],
  },
]

/** future 标志：提前启用 React Router v7 的行为，避免控制台的升级警告 */
export const createAppRouter = () =>
  createBrowserRouter(routes, {
    future: {
      v7_relativeSplatPath: true,
      v7_fetcherPersist: true,
      v7_normalizeFormMethod: true,
      v7_partialHydration: true,
      v7_skipActionErrorRevalidation: true,
    },
  })
