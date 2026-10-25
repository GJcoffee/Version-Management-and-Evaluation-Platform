import { Link } from 'react-router-dom'
import { FlaskConical } from 'lucide-react'
import { useEvals } from '@/api/queries/evals'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card'
import { EmptyState } from '@/components/ui/EmptyState'
import { LoadingBlock } from '@/components/ui/Spinner'
import { EvalProgressBar, EvalStatusBadge } from './EvalStatus'
import { runDatasetLabel, runTitle, runVersionLabel } from './evalUtils'

/** 首页卡片：正在运行的评测及其进度（每 2 秒刷新） */
export function RunningEvalsCard() {
  const { data, isLoading } = useEvals({ status: 'queued,running,paused', limit: 8 }, { autoRefresh: 2000 })
  const runs = data?.pages[0]?.items ?? []
  return (
    <Card data-testid="running-evals">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <FlaskConical className="h-4 w-4 text-muted-foreground" />
          正在运行的评测
        </CardTitle>
        <Link to="/evals" className="text-xs text-primary hover:underline">
          全部评测
        </Link>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <LoadingBlock className="py-6" />
        ) : runs.length === 0 ? (
          <EmptyState icon={FlaskConical} title="没有正在运行的评测" description="在 Agent 的版本列表中点击“评测”即可发起。" className="py-6" />
        ) : (
          <ul className="divide-y divide-border">
            {runs.map((r) => (
              <li key={r.id}>
                <Link to={`/evals/${r.id}`} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-1 py-2 text-sm hover:bg-accent/60">
                  <span className="min-w-0 max-w-64 truncate font-medium">{runTitle(r)}</span>
                  <EvalStatusBadge status={r.status} />
                  <span className="font-mono text-xs text-muted-foreground">
                    {r.agent_name} {runVersionLabel(r)} · {runDatasetLabel(r)}
                  </span>
                  <EvalProgressBar progress={r.progress} className="ml-auto w-56" />
                </Link>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  )
}
