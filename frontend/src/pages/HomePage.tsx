import type { ReactNode } from 'react'
import { Activity, Cpu, Database, ListChecks } from 'lucide-react'
import { useHealth } from '@/api/queries/health'
import { ApiError } from '@/api/client'
import { Badge } from '@/components/ui/Badge'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/Card'
import { EmptyState } from '@/components/ui/EmptyState'
import { LoadingBlock } from '@/components/ui/Spinner'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/Table'
import { cn } from '@/lib/cn'
import { formatBytes } from '@/lib/format'
import type { HealthOut } from '@/api/types'

function Stat({ label, value, hint }: { label: string; value: ReactNode; hint?: ReactNode }) {
  return (
    <div>
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="mt-0.5 font-mono text-2xl font-semibold tabular-nums">{value}</div>
      {hint && <div className="mt-0.5 text-xs text-muted-foreground">{hint}</div>}
    </div>
  )
}

function CardTitleRow({ icon: Icon, children }: { icon: typeof Activity; children: ReactNode }) {
  return (
    <CardTitle className="flex items-center gap-2">
      <Icon className="h-4 w-4 text-muted-foreground" />
      {children}
    </CardTitle>
  )
}

const limit = (v: number | null | undefined) => (v === null || v === undefined ? '不限' : v.toLocaleString())

function HealthCards({ h }: { h: HealthOut }) {
  const busyPct = h.workers.total > 0 ? Math.min(100, (h.workers.busy / h.workers.total) * 100) : 0
  return (
    <>
      <div className="grid gap-4 md:grid-cols-3">
        <Card>
          <CardHeader>
            <CardTitleRow icon={ListChecks}>任务队列</CardTitleRow>
          </CardHeader>
          <CardContent className="flex gap-8">
            <Stat label="排队中" value={h.queue.pending} />
            <Stat label="执行中" value={h.queue.running} />
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitleRow icon={Cpu}>Worker</CardTitleRow>
          </CardHeader>
          <CardContent>
            <Stat
              label="忙碌 / 总数"
              value={
                <>
                  {h.workers.busy}
                  <span className="text-base text-muted-foreground"> / {h.workers.total}</span>
                </>
              }
            />
            <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-muted" aria-hidden="true">
              <div
                className={cn('h-full rounded-full transition-all', busyPct >= 90 ? 'bg-warning' : 'bg-primary')}
                style={{ width: `${busyPct}%` }}
              />
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitleRow icon={Database}>系统</CardTitleRow>
            <Badge variant={h.ok ? 'success' : 'danger'}>{h.ok ? '正常' : '异常'}</Badge>
          </CardHeader>
          <CardContent className="flex gap-8">
            <Stat label="版本" value={<span className="text-lg">{h.version}</span>} />
            <Stat label="数据库" value={<span className="text-lg">{formatBytes(h.db_size)}</span>} />
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <div>
            <CardTitleRow icon={Activity}>Provider 状态</CardTitleRow>
            <CardDescription>实时并发与限流余量（每 5 秒刷新）</CardDescription>
          </div>
        </CardHeader>
        <CardContent>
          {h.providers.length === 0 ? (
            <EmptyState title="尚未配置 Provider" description="前往「设置」添加模型服务后，这里会显示并发和限流余量。" className="py-6" />
          ) : (
            <Table>
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead>名称</TableHead>
                  <TableHead className="text-right">进行中</TableHead>
                  <TableHead className="text-right">RPM 余量</TableHead>
                  <TableHead className="text-right">TPM 余量</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {h.providers.map((p) => (
                  <TableRow key={p.name}>
                    <TableCell className="font-mono text-xs">{p.name}</TableCell>
                    <TableCell className="text-right font-mono tabular-nums">{p.inflight}</TableCell>
                    <TableCell className="text-right font-mono tabular-nums">{limit(p.rpm_left)}</TableCell>
                    <TableCell className="text-right font-mono tabular-nums">{limit(p.tpm_left)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </>
  )
}

/** 首页（框架）：健康状态卡片，每 5 秒轮询。「最近的 agent / 运行中的评测 / 最近的会话」待后续实现 */
export default function HomePage() {
  const { data, error, isLoading } = useHealth()
  return (
    <div className="mx-auto max-w-6xl space-y-4 p-6">
      <h1 className="text-lg font-semibold">首页</h1>
      {isLoading && <LoadingBlock />}
      {error && !data && (
        <Card>
          <CardContent className="pt-4 text-sm text-danger">
            无法获取服务状态：{error instanceof ApiError ? error.message : String(error)}
          </CardContent>
        </Card>
      )}
      {data && <HealthCards h={data} />}
      <Card>
        <CardHeader>
          <CardTitle>最近活动</CardTitle>
        </CardHeader>
        <CardContent>
          <EmptyState
            title="开发中"
            description="最近的 agent、正在运行的评测、最近的会话将在后续阶段显示在这里。"
            className="py-6"
          />
        </CardContent>
      </Card>
    </div>
  )
}
