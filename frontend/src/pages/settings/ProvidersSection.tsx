import { useState } from 'react'
import { Pencil, Play, Plus, Server, Trash2 } from 'lucide-react'
import { ApiError } from '@/api/client'
import { useDeleteProvider, useProviders, useTestProvider } from '@/api/queries/providers'
import type { ProviderOut } from '@/api/types'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/Card'
import { ConfirmDialog } from '@/components/ui/ConfirmDialog'
import { EmptyState } from '@/components/ui/EmptyState'
import { LoadingBlock } from '@/components/ui/Spinner'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/Table'
import { formatDuration } from '@/lib/format'
import { toast } from '@/stores/toast'
import { ProviderDialog } from './ProviderDialog'

const lim = (v: number | null) => (v === null ? '∞' : v.toLocaleString())

export function ProvidersSection() {
  const { data, isLoading, error, refetch } = useProviders()
  const del = useDeleteProvider()
  const test = useTestProvider()
  const [editing, setEditing] = useState<ProviderOut | undefined>()
  const [dialogOpen, setDialogOpen] = useState(false)
  const [deleting, setDeleting] = useState<ProviderOut | null>(null)
  const [testingId, setTestingId] = useState<string | null>(null)

  const openCreate = () => {
    setEditing(undefined)
    setDialogOpen(true)
  }
  const openEdit = (p: ProviderOut) => {
    setEditing(p)
    setDialogOpen(true)
  }

  const runTest = async (p: ProviderOut) => {
    setTestingId(p.id)
    try {
      const r = await test.mutateAsync(p.id)
      if (r.ok) toast.success(`${p.name} 连接成功`, { description: `耗时 ${formatDuration(r.latency_ms)}` })
      else toast.error(`${p.name} 连接失败`, { description: r.error })
    } catch (e) {
      toast.error(`${p.name} 测试失败`, { description: e instanceof ApiError ? e.message : undefined })
    } finally {
      setTestingId(null)
    }
  }

  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle>模型服务（Provider）</CardTitle>
          <CardDescription>配置 OpenAI 兼容的模型服务、模型价格与限流。Agent Spec 通过名称引用。</CardDescription>
        </div>
        <Button size="sm" onClick={openCreate}>
          <Plus className="h-3.5 w-3.5" />
          新建
        </Button>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <LoadingBlock />
        ) : error ? (
          <div className="flex items-center gap-3 py-6 text-sm text-danger">
            加载失败：{error instanceof ApiError ? error.message : String(error)}
            <Button size="sm" variant="outline" onClick={() => refetch()}>
              重试
            </Button>
          </div>
        ) : !data || data.length === 0 ? (
          <EmptyState
            icon={Server}
            title="还没有 Provider"
            description="添加一个模型服务后，Agent 才能调用模型。"
            action={
              <Button size="sm" onClick={openCreate}>
                <Plus className="h-3.5 w-3.5" />
                新建 Provider
              </Button>
            }
          />
        ) : (
          <Table>
            <TableHeader>
              <TableRow className="hover:bg-transparent">
                <TableHead>名称</TableHead>
                <TableHead>Base URL</TableHead>
                <TableHead>密钥</TableHead>
                <TableHead>模型</TableHead>
                <TableHead title="RPM / TPM / 并发">限流</TableHead>
                <TableHead className="text-right">操作</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.map((p) => (
                <TableRow key={p.id}>
                  <TableCell className="font-mono text-xs font-medium">{p.name}</TableCell>
                  <TableCell className="max-w-64 truncate font-mono text-xs text-muted-foreground" title={p.base_url}>
                    {p.base_url}
                  </TableCell>
                  <TableCell>
                    <Badge variant={p.has_api_key ? 'success' : 'secondary'}>{p.has_api_key ? '已设置' : '未设置'}</Badge>
                  </TableCell>
                  <TableCell className="text-xs" title={p.models.map((m) => m.name).join('\n')}>
                    <span className="font-mono">{p.models.length}</span> 个
                    {p.models.length > 0 && (
                      <span className="ml-1.5 text-muted-foreground">
                        {p.models
                          .slice(0, 2)
                          .map((m) => m.name)
                          .join('、')}
                        {p.models.length > 2 && '…'}
                      </span>
                    )}
                  </TableCell>
                  <TableCell className="whitespace-nowrap font-mono text-xs text-muted-foreground">
                    {lim(p.rpm_limit)} / {lim(p.tpm_limit)} / {p.max_concurrency}
                  </TableCell>
                  <TableCell>
                    <div className="flex justify-end gap-1">
                      <Button size="sm" variant="ghost" onClick={() => runTest(p)} loading={testingId === p.id}>
                        {testingId !== p.id && <Play className="h-3.5 w-3.5" />}
                        测试
                      </Button>
                      <Button size="icon" variant="ghost" aria-label={`编辑 ${p.name}`} onClick={() => openEdit(p)}>
                        <Pencil className="h-4 w-4" />
                      </Button>
                      <Button size="icon" variant="ghost" aria-label={`删除 ${p.name}`} onClick={() => setDeleting(p)}>
                        <Trash2 className="h-4 w-4 text-danger" />
                      </Button>
                    </div>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>

      <ProviderDialog open={dialogOpen} onOpenChange={setDialogOpen} provider={editing} />

      <ConfirmDialog
        open={!!deleting}
        onOpenChange={(v) => !v && setDeleting(null)}
        danger
        title={`删除 Provider「${deleting?.name ?? ''}」？`}
        description="删除后，引用该 Provider 的 Agent 草稿和评估器将无法运行。此操作不可撤销。"
        confirmText="删除"
        onConfirm={async () => {
          if (!deleting) return
          try {
            await del.mutateAsync(deleting.id)
            toast.success(`已删除 ${deleting.name}`)
          } catch (e) {
            toast.error('删除失败', { description: e instanceof ApiError ? e.message : undefined })
            throw e
          }
        }}
      />
    </Card>
  )
}
