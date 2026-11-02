import { useState } from 'react'
import { ApiError } from '@/api/client'
import {
  ENVIRONMENT_LABEL, SOURCE_LABEL, useAdapterEnvironments, useEnvironmentRevisions, usePushToProduction,
  useRollbackProduction, useUpdateTest,
  type AdapterBinding, type AdapterEnvironmentName, type EnvironmentChange, type EnvironmentState,
} from '@/api/queries/adapters'
import { AdapterBindingFields } from '@/components/AdapterBindingFields'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/Card'
import { Dialog } from '@/components/ui/Dialog'
import { Field, Input } from '@/components/ui/Input'
import { RelativeTime } from '@/components/ui/RelativeTime'
import { LoadingBlock } from '@/components/ui/Spinner'
import { Select } from '@/components/ui/Select'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/Table'
import { bindingSummary } from '@/lib/adapterEnvironment'
import { formatApiError } from '@/lib/apiError'
import { toast } from '@/stores/toast'

/** 保存 / 推送 / 回滚的结果提示：内容没变不产生新存档 */
function report(change: EnvironmentChange, done: string) {
  if (change.applied) toast.success(`${done}：${ENVIRONMENT_LABEL[change.environment]}存档 #${change.target.revision}`)
  else toast.show(change.message ?? '内容未变化')
}

function EnvironmentCard({ state, children }: { state: EnvironmentState; children?: React.ReactNode }) {
  const name = state.environment
  return (
    <div className="space-y-2 rounded-lg border border-border p-3" data-testid={`env-card-${name}`}>
      <div className="flex items-center gap-2">
        <h4 className="text-sm font-semibold">{ENVIRONMENT_LABEL[name]}</h4>
        {state.configured
          ? <Badge variant="outline" data-testid={`env-revision-${name}`}>存档 #{state.revision}</Badge>
          : <Badge variant="secondary">未配置</Badge>}
      </div>
      {state.binding ? (
        <dl className="space-y-1 text-xs">
          <div className="flex gap-2"><dt className="w-16 shrink-0 text-muted-foreground">适配器</dt>
            <dd>{bindingSummary(state.binding)}</dd></div>
          <div className="flex gap-2"><dt className="w-16 shrink-0 text-muted-foreground">服务地址</dt>
            <dd className="break-all font-mono">{String(state.binding.config.base_url ?? '-')}</dd></div>
          <div className="flex gap-2"><dt className="w-16 shrink-0 text-muted-foreground">存档说明</dt>
            <dd>{state.reason}</dd></div>
          <div className="flex gap-2"><dt className="w-16 shrink-0 text-muted-foreground">更新时间</dt>
            <dd>{state.updated_at ? <RelativeTime value={state.updated_at} /> : '-'}</dd></div>
        </dl>
      ) : (
        <p className="text-xs text-muted-foreground">
          {name === 'test' ? '尚未配置；编辑后可在测试上验证，再推送到生产。' : '尚未配置；先在测试中配置，验证后推送到生产。'}
        </p>
      )}
      {children}
    </div>
  )
}

function TestEditor({ state, onDone }: { state: EnvironmentState; onDone: () => void }) {
  const update = useUpdateTest()
  const [binding, setBinding] = useState<AdapterBinding | null>(state.binding)
  const [reason, setReason] = useState('')
  const [error, setError] = useState('')
  const save = async () => {
    setError('')
    try {
      report(await update.mutateAsync({ binding: binding!, expected_revision: state.revision, reason }), '已保存')
      onDone()
    } catch (e) {
      const f = formatApiError(e, '保存失败')
      setError([f.message, ...f.lines].join('\n'))
    }
  }
  return (
    <div className="space-y-3 pt-2" data-testid="test-editor">
      <AdapterBindingFields value={binding} onChange={setBinding} />
      <Field label="存档说明" htmlFor="env-test-reason" required hint="记录这次为什么改">
        <Input id="env-test-reason" value={reason} onChange={(e) => setReason(e.target.value)} />
      </Field>
      {error && <p role="alert" className="whitespace-pre-wrap text-xs text-danger">{error}</p>}
      <div className="flex gap-2">
        <Button size="sm" onClick={() => void save()} loading={update.isPending}
          disabled={!binding?.credential_ref || !reason.trim()}>保存到测试</Button>
        <Button size="sm" variant="ghost" onClick={onDone}>取消</Button>
      </div>
    </div>
  )
}

function PushDialog({ test, onClose }: { test: EnvironmentState; onClose: () => void }) {
  const push = usePushToProduction()
  const [reason, setReason] = useState('')
  const submit = async () => {
    try {
      report(await push.mutateAsync({ test_revision: test.revision, reason: reason.trim() || undefined }), '已推送')
      onClose()
    } catch (e) {
      toast.error('推送失败', { description: e instanceof ApiError ? e.message : undefined })
    }
  }
  return (
    <Dialog open onOpenChange={(v) => !v && onClose()} size="sm" title="推送到生产"
      description={`把测试的当前配置（存档 #${test.revision}）复制为生产的新存档；之后新建的对话和评测默认使用它，已有会话不受影响。`}
      footer={<>
        <Button variant="outline" onClick={onClose}>取消</Button>
        <Button onClick={() => void submit()} loading={push.isPending} data-testid="confirm-push">推送</Button>
      </>}>
      {test.binding && <p className="mb-3 text-xs text-muted-foreground">{bindingSummary(test.binding)}</p>}
      <Field label="备注" htmlFor="env-push-reason" hint="可选">
        <Input id="env-push-reason" value={reason} onChange={(e) => setReason(e.target.value)} />
      </Field>
    </Dialog>
  )
}

function RollbackDialog({ production, onClose }: { production: EnvironmentState; onClose: () => void }) {
  const revisions = useEnvironmentRevisions('production')
  const rollback = useRollbackProduction()
  const [picked, setPicked] = useState('')
  const [reason, setReason] = useState('')
  const options = (revisions.data ?? []).filter((r) => r.revision !== production.revision)
  const target = picked || String(options[0]?.revision ?? '')
  const submit = async () => {
    try {
      report(await rollback.mutateAsync({ revision: Number(target), reason: reason.trim() || undefined }), '已回滚')
      onClose()
    } catch (e) {
      toast.error('回滚失败', { description: e instanceof ApiError ? e.message : undefined })
    }
  }
  return (
    <Dialog open onOpenChange={(v) => !v && onClose()} size="md" title="回滚生产"
      description="生产回到所选的历史存档：把它的配置追加为新存档，历史不会被改写。"
      footer={<>
        <Button variant="outline" onClick={onClose}>取消</Button>
        <Button onClick={() => void submit()} loading={rollback.isPending} disabled={!target} data-testid="confirm-rollback">
          回滚
        </Button>
      </>}>
      {revisions.isLoading ? <LoadingBlock /> : options.length === 0
        ? <p className="text-sm text-muted-foreground">没有其他历史存档可回滚。</p>
        : <div className="space-y-3">
          <Field label="回到存档" htmlFor="env-rollback-target">
            <Select id="env-rollback-target" value={target} onChange={(e) => setPicked(e.target.value)}>
              {options.map((r) => <option key={r.revision} value={r.revision}>
                #{r.revision} · {bindingSummary(r.binding)} · {r.reason}
              </option>)}
            </Select>
          </Field>
          <Field label="备注" htmlFor="env-rollback-reason" hint="可选">
            <Input id="env-rollback-reason" value={reason} onChange={(e) => setReason(e.target.value)} />
          </Field>
        </div>}
    </Dialog>
  )
}

function History({ name }: { name: AdapterEnvironmentName }) {
  const revisions = useEnvironmentRevisions(name)
  return (
    <div className="space-y-1.5" data-testid={`env-history-${name}`}>
      <h4 className="text-sm font-semibold">{ENVIRONMENT_LABEL[name]}存档历史</h4>
      {revisions.isLoading ? <LoadingBlock /> : !revisions.data?.length
        ? <p className="text-xs text-muted-foreground">还没有存档。</p>
        : <Table>
          <TableHeader>
            <TableRow className="hover:bg-transparent">
              <TableHead>存档</TableHead><TableHead>来源</TableHead><TableHead>适配器与配置</TableHead>
              <TableHead>说明</TableHead><TableHead>时间</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {revisions.data.map((r) => <TableRow key={r.revision}>
              <TableCell className="font-mono text-xs">
                #{r.revision}{r.current && <Badge variant="success" className="ml-1">当前</Badge>}
              </TableCell>
              <TableCell className="text-xs">{SOURCE_LABEL[r.source]}</TableCell>
              <TableCell className="text-xs">{bindingSummary(r.binding)}</TableCell>
              <TableCell className="max-w-64 truncate text-xs" title={r.reason}>{r.reason}</TableCell>
              <TableCell className="text-xs text-muted-foreground"><RelativeTime value={r.created_at} /></TableCell>
            </TableRow>)}
          </TableBody>
        </Table>}
    </div>
  )
}

export function AdapterSection() {
  const { data, isLoading, error } = useAdapterEnvironments()
  const [editing, setEditing] = useState(false)
  const [dialog, setDialog] = useState<'push' | 'rollback' | null>(null)
  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle>适配器</CardTitle>
          <CardDescription>
            全平台只有生产和测试两套接入配置，所有「由平台托管适配器」的版本都使用它们，新发布的版本自动沿用。
            默认使用生产；改动适配器时只编辑测试，验证后推送到生产。
          </CardDescription>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        {isLoading ? <LoadingBlock /> : error || !data
          ? <p className="py-4 text-sm text-danger">加载失败：{error instanceof ApiError ? error.message : '未知错误'}</p>
          : <>
            <div className="grid gap-4 md:grid-cols-2">
              <EnvironmentCard state={data.production}>
                <Button size="sm" variant="outline" disabled={data.production.revision === 0}
                  onClick={() => setDialog('rollback')} data-testid="open-rollback">回滚</Button>
              </EnvironmentCard>
              <EnvironmentCard state={data.test}>
                {editing
                  ? <TestEditor key={data.test.revision} state={data.test} onDone={() => setEditing(false)} />
                  : <div className="flex gap-2">
                    <Button size="sm" variant="outline" onClick={() => setEditing(true)} data-testid="edit-test">
                      {data.test.configured ? '编辑' : '配置'}
                    </Button>
                    <Button size="sm" disabled={!data.test.configured || !data.test_selectable}
                      title={data.test.configured && !data.test_selectable ? '测试与生产内容相同，无需推送' : undefined}
                      onClick={() => setDialog('push')} data-testid="open-push">推送到生产</Button>
                  </div>}
              </EnvironmentCard>
            </div>
            <div className="grid gap-4 lg:grid-cols-2">
              <History name="production" />
              <History name="test" />
            </div>
            {dialog === 'push' && <PushDialog test={data.test} onClose={() => setDialog(null)} />}
            {dialog === 'rollback' && <RollbackDialog production={data.production} onClose={() => setDialog(null)} />}
          </>}
      </CardContent>
    </Card>
  )
}
