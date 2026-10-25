import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAgent, useAgents } from '@/api/queries/agents'
import { useAssetRevisions, useAssets } from '@/api/queries/assets'
import { useDatasetCases } from '@/api/queries/datasets'
import { useCreateEval } from '@/api/queries/evals'
import { useVersions } from '@/api/queries/versions'
import type { VersionSummary } from '@/api/types'
import { AssetRefPicker } from '@/components/AssetRefPicker'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { Dialog } from '@/components/ui/Dialog'
import { Field, Input } from '@/components/ui/Input'
import { Select } from '@/components/ui/Select'
import { formatApiError } from '@/lib/apiError'
import { parseAssetRef } from '@/lib/assetRef'
import { cn } from '@/lib/cn'
import { toast } from '@/stores/toast'
import { EvaluatorEditor } from './EvaluatorEditor'
import { evaluatorToInput, newEvaluatorForm, validateEvaluators, type EvaluatorForm } from './evalUtils'

/** 版本选项的键：`ver:<id>` | `tag:<名称>` | `latest` */
export function versionRefsFromSelection(selection: readonly string[], agentName: string): string[] {
  return selection.map((k) => {
    if (k.startsWith('ver:')) return k.slice(4)
    if (k.startsWith('tag:')) return `${agentName}:${k.slice(4)}`
    return `${agentName}:latest`
  })
}

/** 解析所选数据集：资产、修订（含用例数） */
function useDatasetInfo(ref: string | null) {
  const parsed = useMemo(() => parseAssetRef(ref), [ref])
  const assets = useAssets({ kind: 'dataset', q: parsed?.name, limit: 50 }, !!parsed)
  const asset = parsed ? assets.data?.pages.flatMap((p) => p.items).find((a) => a.name === parsed.name) : undefined
  const revs = useAssetRevisions(asset?.id, 200)
  const items = revs.data?.pages.flatMap((p) => p.items) ?? []
  const rev = !parsed || !asset ? undefined : parsed.rev === 'latest' ? (items.find((r) => r.rev === asset.latest_rev) ?? asset.latest ?? undefined) : items.find((r) => r.rev === parsed.rev)
  // 取前 200 个用例用于列出标签；用例更多时只给出已加载部分的标签
  const cases = useDatasetCases(rev?.id, {}, 200)
  const loaded = cases.data?.pages.flatMap((p) => p.items) ?? []
  const tagCount = new Map<string, number>()
  for (const c of loaded) for (const t of c.tags) tagCount.set(t, (tagCount.get(t) ?? 0) + 1)
  return {
    rev,
    caseCount: typeof rev?.meta.case_count === 'number' ? rev.meta.case_count : undefined,
    tags: [...tagCount.entries()].sort((a, b) => b[1] - a[1]),
    loaded,
    complete: cases.isSuccess && !cases.hasNextPage,
  }
}

function toPositiveInt(s: string): number | null {
  const n = Number(s)
  return Number.isInteger(n) && n >= 1 ? n : null
}

export interface NewEvalDialogProps {
  /** 固定 agent（Agent 详情页 / 版本列表入口）；缺省时在对话框中选择 */
  agentId?: string
  /** 预选的版本 id */
  defaultVersionIds?: string[]
  onClose: () => void
}

/** 发起评测：版本（可多选）+ 数据集（可按标签筛选用例）+ 评估器 + 重复次数 / 并发 */
export function NewEvalDialog({ agentId: fixedAgentId, defaultVersionIds, onClose }: NewEvalDialogProps) {
  const navigate = useNavigate()
  const create = useCreateEval()
  const [pickedAgent, setPickedAgent] = useState('')
  const agentId = fixedAgentId ?? pickedAgent
  const agents = useAgents()
  const agent = useAgent(agentId || undefined)
  const versionsQ = useVersions(agentId || undefined, 200)
  const versions: VersionSummary[] = useMemo(() => versionsQ.data?.pages.flatMap((p) => p.items) ?? [], [versionsQ.data])

  const [selection, setSelection] = useState<string[]>(() => (defaultVersionIds ?? []).map((id) => `ver:${id}`))
  const [name, setName] = useState('')
  const [dataset, setDataset] = useState<string | null>(null)
  const [tagFilter, setTagFilter] = useState<string[]>([])
  const [evaluators, setEvaluators] = useState<EvaluatorForm[]>(() => [newEvaluatorForm('exact_match', [])])
  const [repeats, setRepeats] = useState('1')
  const [concurrency, setConcurrency] = useState('8')
  const [showErrors, setShowErrors] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const ds = useDatasetInfo(dataset)
  const tags = useMemo(() => {
    const seen = new Map<string, number>()
    for (const v of versions) for (const t of v.tags) if (!seen.has(t)) seen.set(t, v.seq)
    return [...seen.entries()]
  }, [versions])
  const latestSeq = versions.length > 0 ? Math.max(...versions.map((v) => v.seq)) : undefined

  const toggleVersion = (key: string) => setSelection((cur) => (cur.includes(key) ? cur.filter((k) => k !== key) : [...cur, key]))
  const toggleTag = (t: string) => setTagFilter((cur) => (cur.includes(t) ? cur.filter((x) => x !== t) : [...cur, t]))

  const matched = tagFilter.length > 0 && ds.complete ? ds.loaded.filter((c) => c.tags.some((t) => tagFilter.includes(t))).length : undefined
  const totalCases = tagFilter.length > 0 ? matched : ds.caseCount

  const repeatsN = toPositiveInt(repeats)
  const concurrencyN = toPositiveInt(concurrency)
  const problems: string[] = []
  if (!agentId) problems.push('请选择 Agent')
  else if (selection.length === 0) problems.push('请至少选择一个版本')
  if (!dataset) problems.push('请选择数据集')
  if (repeatsN === null) problems.push('重复次数必须是不小于 1 的整数')
  if (concurrencyN === null) problems.push('并发数必须是不小于 1 的整数')
  const evValidation = validateEvaluators(evaluators)
  const evInvalid = !!evValidation.global || Object.keys(evValidation.byUid).length > 0

  const submit = async () => {
    setShowErrors(true)
    setError(null)
    if (problems.length > 0 || evInvalid || !agent.data || !dataset) return
    try {
      const r = await create.mutateAsync({
        name: name.trim() || undefined,
        version_ids: versionRefsFromSelection(selection, agent.data.name),
        dataset,
        case_filter: tagFilter.length > 0 ? { tags: tagFilter } : undefined,
        evaluators: evaluators.map(evaluatorToInput),
        repeats: repeatsN!,
        concurrency: concurrencyN!,
      })
      toast.success(r.runs.length > 1 ? `已发起 ${r.runs.length} 个评测` : '已发起评测')
      onClose()
      navigate(`/evals/${r.runs[0].id}`)
    } catch (e) {
      const f = formatApiError(e, '发起评测失败')
      setError([f.message, ...f.lines].join('\n'))
    }
  }

  const agentList = (agents.data?.items ?? []).filter((a) => !a.archived)

  return (
    <Dialog
      open
      onOpenChange={(v) => !v && onClose()}
      size="xl"
      title="发起评测"
      description="每个所选版本会创建一个评测 run，使用同一份数据集和评估器；完成后可以一键对比。"
      footer={
        <>
          {showErrors && problems.length > 0 && <span className="mr-auto text-xs text-danger">{problems[0]}</span>}
          <Button variant="outline" onClick={onClose}>
            取消
          </Button>
          <Button onClick={() => void submit()} loading={create.isPending} data-testid="submit-eval">
            开始评测{selection.length > 1 ? `（${selection.length} 个版本）` : ''}
          </Button>
        </>
      }
    >
      <form
        className="space-y-5"
        onSubmit={(e) => {
          e.preventDefault()
          void submit()
        }}
      >
        <section className="space-y-3">
          <h3 className="text-sm font-semibold">1. 被测版本</h3>
          {!fixedAgentId && (
            <Field label="Agent" required htmlFor="ne-agent">
              <Select
                id="ne-agent"
                value={pickedAgent}
                onChange={(e) => {
                  setPickedAgent(e.target.value)
                  setSelection([])
                }}
              >
                <option value="">选择 Agent…</option>
                {agentList.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.name}
                  </option>
                ))}
              </Select>
            </Field>
          )}
          {agentId && (
            <div className="space-y-2" data-testid="version-picker">
              {versionsQ.isLoading && <p className="text-xs text-muted-foreground">加载版本…</p>}
              {versionsQ.isSuccess && versions.length === 0 && (
                <p className="rounded-md border border-warning/40 bg-warning/10 px-3 py-2 text-xs text-warning">这个 Agent 还没有发布过版本，请先发布。</p>
              )}
              <div className="flex flex-wrap gap-1.5">
                {versions.length > 0 && (
                  <VersionChip
                    checked={selection.includes('latest')}
                    onToggle={() => toggleVersion('latest')}
                    label={`最新版本${latestSeq !== undefined ? `（v${latestSeq}）` : ''}`}
                    testId="ver-latest"
                  />
                )}
                {tags.map(([t, seq]) => (
                  <VersionChip key={`tag:${t}`} checked={selection.includes(`tag:${t}`)} onToggle={() => toggleVersion(`tag:${t}`)} label={`标签 ${t}（v${seq}）`} testId={`ver-tag-${t}`} />
                ))}
              </div>
              <div className="flex max-h-40 flex-wrap gap-1.5 overflow-y-auto">
                {versions.map((v) => (
                  <VersionChip
                    key={v.id}
                    checked={selection.includes(`ver:${v.id}`)}
                    onToggle={() => toggleVersion(`ver:${v.id}`)}
                    label={`v${v.seq}`}
                    title={v.notes ?? undefined}
                    sub={v.tags.join(', ')}
                    testId={`ver-v${v.seq}`}
                  />
                ))}
              </div>
              <p className="text-xs text-muted-foreground">可多选；选多个版本时每个版本一个 run，便于直接对比。</p>
            </div>
          )}
        </section>

        <section className="space-y-3">
          <h3 className="text-sm font-semibold">2. 数据集</h3>
          <Field label="数据集与修订" required hint={ds.caseCount !== undefined ? `该修订共 ${ds.caseCount} 个用例` : undefined}>
            <AssetRefPicker
              kind="dataset"
              value={dataset}
              onChange={(r) => {
                setDataset(r)
                setTagFilter([])
              }}
              placeholder="选择数据集…"
            />
          </Field>
          {dataset && ds.tags.length > 0 && (
            <div className="space-y-1.5" data-testid="tag-filter">
              <div className="text-xs font-medium">按标签筛选用例（含任一标签；不选则全部）</div>
              <div className="flex flex-wrap gap-1.5">
                {ds.tags.map(([t, n]) => (
                  <button
                    key={t}
                    type="button"
                    aria-pressed={tagFilter.includes(t)}
                    onClick={() => toggleTag(t)}
                    className={cn(
                      'rounded border px-2 py-0.5 font-mono text-xs transition-colors',
                      tagFilter.includes(t) ? 'border-primary bg-primary/10 text-primary' : 'border-border hover:bg-accent',
                    )}
                  >
                    {t}
                    {ds.complete && <span className="ml-1 text-muted-foreground">{n}</span>}
                  </button>
                ))}
              </div>
              {tagFilter.length > 0 && (
                <p className="text-xs text-muted-foreground">
                  {totalCases !== undefined ? `将评测 ${totalCases} 个用例` : '将只评测带有所选标签的用例'}
                  {totalCases !== undefined && repeatsN && repeatsN > 1 ? `，每个重复 ${repeatsN} 次` : ''}
                </p>
              )}
            </div>
          )}
        </section>

        <section className="space-y-3">
          <h3 className="text-sm font-semibold">3. 评估器</h3>
          <EvaluatorEditor value={evaluators} onChange={setEvaluators} showErrors={showErrors} />
        </section>

        <section className="space-y-3">
          <h3 className="text-sm font-semibold">4. 运行参数</h3>
          <div className="grid grid-cols-3 gap-3">
            <Field label="名称" htmlFor="ne-name" hint="可选；缺省为 Agent + 版本">
              <Input id="ne-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="v13 回归" />
            </Field>
            <Field label="重复次数 repeats" htmlFor="ne-repeats" error={showErrors && repeatsN === null ? '请输入不小于 1 的整数' : null}>
              <Input id="ne-repeats" type="number" min={1} value={repeats} onChange={(e) => setRepeats(e.target.value)} />
            </Field>
            <Field label="并发 concurrency" htmlFor="ne-conc" error={showErrors && concurrencyN === null ? '请输入不小于 1 的整数' : null}>
              <Input id="ne-conc" type="number" min={1} value={concurrency} onChange={(e) => setConcurrency(e.target.value)} />
            </Field>
          </div>
        </section>

        {error && (
          <p role="alert" className="whitespace-pre-wrap rounded-md border border-danger/40 bg-danger/10 px-3 py-2 text-xs text-danger">
            {error}
          </p>
        )}
        <button type="submit" className="hidden" />
      </form>
    </Dialog>
  )
}

function VersionChip({
  checked,
  onToggle,
  label,
  sub,
  title,
  testId,
}: {
  checked: boolean
  onToggle: () => void
  label: string
  sub?: string
  title?: string
  testId?: string
}) {
  return (
    <label
      title={title}
      data-testid={testId}
      className={cn(
        'inline-flex cursor-pointer items-center gap-1.5 rounded-md border px-2 py-1 text-xs transition-colors',
        checked ? 'border-primary bg-primary/10 text-foreground' : 'border-border hover:bg-accent',
      )}
    >
      <input type="checkbox" className="h-3.5 w-3.5 accent-primary" checked={checked} onChange={onToggle} />
      <span className="font-mono">{label}</span>
      {sub && <Badge variant="info" className="font-mono">{sub}</Badge>}
    </label>
  )
}
