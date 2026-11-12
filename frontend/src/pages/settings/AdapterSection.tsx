import { useState } from 'react'
import { ChevronDown, ChevronRight } from 'lucide-react'
import { ApiError } from '@/api/client'
import {
  ENVIRONMENT_LABEL, SOURCE_LABEL, useAdapterEnvironments, useAdapters, useEnvironmentRevisions,
  usePushToProduction, useRollbackProduction, useUpdateTest,
  type AdapterBinding, type AdapterDefinition, type AdapterEnvironmentName, type EnvironmentChange,
  type EnvironmentState,
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
import { PlatformAccountSection } from './PlatformAccountSection'

/** 保存 / 推送 / 回滚的结果提示：内容没变不产生新存档 */
function report(change: EnvironmentChange, done: string) {
  if (change.applied) toast.success(`${done}：${ENVIRONMENT_LABEL[change.environment]}存档 #${change.target.revision}`)
  else toast.show(change.message ?? '内容未变化')
}

function EnvironmentCard({ state, definition, children }: {
  state: EnvironmentState
  definition?: AdapterDefinition
  children?: React.ReactNode
}) {
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
          {definition && <>
            <div className="flex gap-2"><dt className="w-16 shrink-0 text-muted-foreground">实现</dt>
              <dd className="font-mono">v{definition.major} · {definition.impl_digest.slice(0, 8)}</dd></div>
            <div className="flex gap-2"><dt className="w-16 shrink-0 text-muted-foreground">能力</dt>
              <dd>{definition.capabilities.join(' · ')}</dd></div>
          </>}
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

function TestEditor({ state, definition, onDone }: { state: EnvironmentState; definition: AdapterDefinition; onDone: () => void }) {
  const update = useUpdateTest(definition.adapter)
  const [binding, setBinding] = useState<AdapterBinding | null>(state.binding ?? {
    adapter: definition.adapter, major: definition.major, credential_ref: '',
    config: Object.fromEntries(Object.entries(definition.config_schema.properties)
      .filter(([, property]) => property.default !== undefined)
      .map(([key, property]) => [key, property.default])),
  })
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
      <AdapterBindingFields value={binding} onChange={setBinding} platform={definition.adapter} />
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

function PushDialog({ test, platform, onClose }: { test: EnvironmentState; platform: string; onClose: () => void }) {
  const push = usePushToProduction(platform)
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

function RollbackDialog({ production, platform, onClose }: { production: EnvironmentState; platform: string; onClose: () => void }) {
  const revisions = useEnvironmentRevisions('production', platform)
  const rollback = useRollbackProduction(platform)
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

function History({ name, platform }: { name: AdapterEnvironmentName; platform: string }) {
  const revisions = useEnvironmentRevisions(name, platform)
  const [expanded, setExpanded] = useState(false)
  const [picked, setPicked] = useState('')
  const items = [...(revisions.data ?? [])].sort((a, b) => b.revision - a.revision)
  const selected = (expanded && items.find((r) => String(r.revision) === picked)) || items[0]
  const label = `${ENVIRONMENT_LABEL[name]}存档历史`
  return (
    <div className="space-y-1.5" data-testid={`env-history-${name}`}>
      <h4>
        <button type="button" className="flex items-center gap-1.5 text-sm font-semibold"
          aria-expanded={expanded} aria-controls={`env-history-content-${name}`}
          onClick={() => { setExpanded(!expanded); setPicked('') }}>
          {expanded ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
          {label}
          {!!items.length && <span className="text-xs font-normal text-muted-foreground">{items.length} 个存档</span>}
        </button>
      </h4>
      {expanded && items.length > 1 && (
        <Select aria-label={`选择${ENVIRONMENT_LABEL[name]}存档`} value={String(selected.revision)}
          onChange={(e) => setPicked(e.target.value)}>
          {items.map((r) => <option key={r.revision} value={r.revision}>
            #{r.revision}{r.revision === items[0].revision ? ' · 最新' : ''}{r.current ? ' · 当前' : ''} · {r.reason}
          </option>)}
        </Select>
      )}
      <div id={`env-history-content-${name}`}>
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
            {[selected].map((r) => <TableRow key={r.revision}>
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
    </div>
  )
}

function PlatformSettings({ definition }: { definition: AdapterDefinition }) {
  const platform = definition.adapter
  const { data, isLoading, error } = useAdapterEnvironments(platform)
  const adapters = useAdapters()
  const [editing, setEditing] = useState(false)
  const [dialog, setDialog] = useState<'push' | 'rollback' | null>(null)
  const definitionOf = (binding: AdapterBinding | null) => binding
    ? adapters.data?.find((a) => a.adapter === binding.adapter && a.major === binding.major)
    : undefined
  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle>适配器</CardTitle>
          <CardDescription>
            {definition.name} 的生产／测试连接与凭据独立维护。先更新测试并检查上游认证，再推送生产。
            失败或缺少明细的旧轮次可使用最新配置重新获取 Trace。
          </CardDescription>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        {isLoading ? <LoadingBlock /> : error || !data
          ? <p className="py-4 text-sm text-danger">加载失败：{error instanceof ApiError ? error.message : '未知错误'}</p>
          : <>
            <div className="grid gap-4 md:grid-cols-2">
              <EnvironmentCard state={data.production} definition={definitionOf(data.production.binding)}>
                <Button size="sm" variant="outline" disabled={data.production.revision === 0}
                  onClick={() => setDialog('rollback')} data-testid="open-rollback">回滚</Button>
              </EnvironmentCard>
              <EnvironmentCard state={data.test} definition={definitionOf(data.test.binding)}>
                {editing
                  ? <TestEditor key={data.test.revision} state={data.test} definition={definition} onDone={() => setEditing(false)} />
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
              <History name="production" platform={platform} />
              <History name="test" platform={platform} />
            </div>
            {dialog === 'push' && <PushDialog test={data.test} platform={platform} onClose={() => setDialog(null)} />}
            {dialog === 'rollback' && <RollbackDialog production={data.production} platform={platform} onClose={() => setDialog(null)} />}
          </>}
      </CardContent>
    </Card>
  )
}

export function AdapterSection() {
  const adapters = useAdapters()
  const [picked, setPicked] = useState('')
  const platforms = [...new Map(adapters.data?.map((item) => [item.adapter, item])).values()]
  const definition = platforms.find((item) => item.adapter === picked) ?? platforms[0]
  if (adapters.isLoading) return <LoadingBlock />
  if (!definition) return <p className="text-sm text-muted-foreground">{adapters.isError ? '平台列表加载失败' : '尚未注册平台适配器'}</p>
  return <div className="space-y-3">
    <nav aria-label="适配器平台" className="flex gap-2 border-b border-border">
      {platforms.map((item) => <button key={item.adapter} type="button" aria-current={definition.adapter === item.adapter ? 'page' : undefined}
        className={`border-b-2 px-3 py-2 text-sm ${definition.adapter === item.adapter ? 'border-primary font-medium text-primary' : 'border-transparent text-muted-foreground'}`}
        onClick={() => setPicked(item.adapter)}>{item.name}</button>)}
    </nav>
    <PlatformSettings key={definition.adapter} definition={definition} />
    {definition.adapter === 'hiagent' && <PlatformAccountSection />}
  </div>
}
