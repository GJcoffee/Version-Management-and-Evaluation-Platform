import { Construction } from 'lucide-react'
import { useLocation, useParams } from 'react-router-dom'
import { Card, CardContent } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'

export interface PagePlaceholderProps {
  title: string
  /** 对应详设中的小节，如 "详设 14.2 DraftEditor" */
  spec?: string
  /** 计划实现的内容要点 */
  planned?: string[]
}

/**
 * 统一的"开发中"占位。后续开发者实现页面时，直接在对应的 src/pages/*.tsx 里
 * 用真实内容替换本组件即可（路由已注册）。
 */
export function PagePlaceholder({ title, spec, planned }: PagePlaceholderProps) {
  const params = useParams()
  const { pathname, search } = useLocation()
  return (
    <div className="mx-auto max-w-3xl p-6">
      <Card>
        <CardContent className="space-y-4 pt-6">
          <div className="flex items-center gap-3">
            <Construction className="h-6 w-6 text-warning" />
            <h1 className="text-lg font-semibold">{title}</h1>
            <Badge variant="warning">开发中</Badge>
          </div>
          <p className="text-sm text-muted-foreground">该页面尚未实现，路由与导航已就绪。</p>
          {spec && <p className="text-xs text-muted-foreground">设计依据：{spec}</p>}
          {planned && planned.length > 0 && (
            <ul className="list-inside list-disc space-y-1 text-sm text-muted-foreground">
              {planned.map((p) => (
                <li key={p}>{p}</li>
              ))}
            </ul>
          )}
          <dl className="grid grid-cols-[5rem_1fr] gap-x-3 gap-y-1 border-t border-border pt-3 font-mono text-xs">
            <dt className="text-muted-foreground">路径</dt>
            <dd>
              {pathname}
              {search}
            </dd>
            {Object.entries(params).map(([k, v]) => (
              <div key={k} className="contents">
                <dt className="text-muted-foreground">:{k}</dt>
                <dd>{v}</dd>
              </div>
            ))}
          </dl>
        </CardContent>
      </Card>
    </div>
  )
}
