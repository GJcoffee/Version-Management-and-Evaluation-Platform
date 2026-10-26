import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { ChevronLeft, ChevronRight, ExternalLink } from 'lucide-react'
import type { EvalItemOut, EvalRunDetailOut } from '@/api/evalTypes'
import { useEvalItem, useEvalItems, useSubmitHumanScores } from '@/api/queries/evals'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card'
import { Input, Textarea } from '@/components/ui/Input'
import { LoadingBlock } from '@/components/ui/Spinner'
import { formatApiError } from '@/lib/apiError'
import { cn } from '@/lib/cn'
import { safeGet, safeSet } from '@/lib/safeStorage'
import { toast } from '@/stores/toast'
import { MediaText } from '@/components/session'
import { ItemConversation } from './ItemDetail'
import {
  annotateProgress,
  buildHumanEntries,
  digitToScore,
  humanEvaluators,
  initialDraft,
  isAnnotatable,
  isPendingHuman,
  scaleOptions,
  scoreInRange,
  type HumanEvaluatorConfig,
  type ScoreDraft,
} from './annotateUtils'

type Mode = 'pending' | 'all'
const ANNOTATOR_KEY = 'evalp.annotator'

function Block({ title, children, testId }: { title: string; children: React.ReactNode; testId?: string }) {
  return (
    <section className="space-y-1.5" data-testid={testId}>
      <h3 className="text-xs font-semibold text-muted-foreground">{title}</h3>
      <div className="whitespace-pre-wrap rounded-md bg-muted/50 px-3 py-2 text-sm">{children}</div>
    </section>
  )
}

/** 输出文本；其中的图片 / 视频链接直接渲染出来，方便人工审核 */
function AnnotateOutput({ text }: { text: string | null | undefined }) {
  if (text === null || text === undefined) return <span className="text-muted-foreground">（没有输出）</span>
  return <MediaText text={text} />
}

function ScoreInput({
  cfg,
  draft,
  active,
  onFocus,
  onChange,
}: {
  cfg: HumanEvaluatorConfig
  draft: ScoreDraft
  active: boolean
  onFocus: () => void
  onChange: (d: ScoreDraft) => void
}) {
  const opts = scaleOptions(cfg.scale)
  return (
    <div className={cn('space-y-2 rounded-md border p-3', active ? 'border-primary' : 'border-border')} onClick={onFocus} data-testid={`human-${cfg.key}`}>
      <div className="flex items-center gap-2">
        <span className="font-mono text-xs font-medium">{cfg.key}</span>
        <span className="text-xs text-muted-foreground">
          量程 {cfg.scale[0]}–{cfg.scale[1]} · 通过线 ≥ {cfg.passThreshold}
        </span>
        {!cfg.gate && <Badge variant="outline">不参与判定</Badge>}
      </div>
      {cfg.guideline && (
        <p className="whitespace-pre-wrap rounded bg-muted/50 px-2 py-1.5 text-xs" data-testid="guideline">
          {cfg.guideline}
        </p>
      )}
      {opts ? (
        <div className="flex flex-wrap gap-1.5" role="group" data-score-group aria-label={`${cfg.key} 打分`}>
          {opts.map((n) => (
            <button
              key={n}
              type="button"
              aria-pressed={draft.score === n}
              onClick={() => onChange({ ...draft, score: n })}
              data-testid={`score-btn-${cfg.key}-${n}`}
              className={cn(
                'h-8 min-w-8 rounded-md border px-2 font-mono text-sm tabular-nums transition-colors',
                draft.score === n ? 'border-primary bg-primary text-primary-foreground' : 'border-border hover:bg-accent',
              )}
            >
              {n}
            </button>
          ))}
        </div>
      ) : (
        <Input
          type="number"
          aria-label={`${cfg.key} 分数`}
          className="w-32"
          min={cfg.scale[0]}
          max={cfg.scale[1]}
          step="any"
          value={draft.score ?? ''}
          onChange={(e) => onChange({ ...draft, score: e.target.value === '' ? null : Number(e.target.value) })}
        />
      )}
      {draft.score !== null && !scoreInRange(draft.score, cfg.scale) && <p className="text-xs text-danger">分数必须在 {cfg.scale[0]} 到 {cfg.scale[1]} 之间</p>}
      <Textarea rows={2} aria-label={`${cfg.key} 备注`} placeholder="备注（可选）：打分理由" value={draft.reason} onChange={(e) => onChange({ ...draft, reason: e.target.value })} />
    </div>
  )
}

/**
 * 人工评审：逐条查看用例的输入 / 输出 / 执行轨迹，给人工评估器打分并备注，提交到 human-scores。
 * 快捷键：数字键打分（作用于高亮的评估器），J / K 切换上一条 / 下一条，Enter 提交，Ctrl+Enter 在备注里提交。
 */
export function AnnotateView({ run }: { run: EvalRunDetailOut }) {
  const humans = useMemo(() => humanEvaluators(run.evaluators), [run.evaluators])
  const keys = useMemo(() => humans.map((h) => h.key), [humans])
  const [mode, setMode] = useState<Mode>('pending')
  const [idx, setIdx] = useState(0)
  const [activeKey, setActiveKey] = useState(keys[0] ?? '')
  const [drafts, setDrafts] = useState<Record<string, Record<string, ScoreDraft>>>({})
  const [submitted, setSubmitted] = useState<Set<string>>(() => new Set())
  const [annotator, setAnnotator] = useState(() => safeGet(ANNOTATOR_KEY) ?? '')
  const [error, setError] = useState<string | null>(null)
  const submit = useSubmitHumanScores()

  const query = useEvalItems(run.id, { cls: mode === 'pending' ? 'pending_human' : '' }, 100)
  const items: EvalItemOut[] = useMemo(() => {
    const all = query.data?.pages.flatMap((p) => p.items) ?? []
    return all.filter((it) => isAnnotatable(it, keys) && (mode === 'all' || (isPendingHuman(it, keys) && !submitted.has(it.id))))
  }, [query.data, keys, mode, submitted])
  const safeIdx = Math.min(idx, Math.max(0, items.length - 1))
  const current = items[safeIdx]
  const detail = useEvalItem(run.id, current?.id)

  // 接近已加载列表末尾时取下一页
  const { hasNextPage, isFetchingNextPage, fetchNextPage } = query
  useEffect(() => {
    if (hasNextPage && !isFetchingNextPage && safeIdx >= items.length - 3) void fetchNextPage()
  }, [hasNextPage, isFetchingNextPage, fetchNextPage, safeIdx, items.length])

  const draftsFor = (item: EvalItemOut): Record<string, ScoreDraft> => {
    const saved = drafts[item.id] ?? {}
    return Object.fromEntries(keys.map((k) => [k, saved[k] ?? initialDraft(item, k)]))
  }
  const currentDrafts = current ? draftsFor(current) : {}
  const setDraft = (key: string, d: ScoreDraft) => {
    if (!current) return
    setDrafts((cur) => ({ ...cur, [current.id]: { ...draftsFor(current), ...(cur[current.id] ?? {}), [key]: d } }))
  }

  const go = useCallback(
    (delta: number) => setIdx((i) => Math.max(0, Math.min(Math.max(0, items.length - 1), Math.min(i, items.length - 1) + delta))),
    [items.length],
  )

  const entries = current ? buildHumanEntries(current.id, currentDrafts, annotator) : []
  const outOfRange = humans.some((h) => {
    const d = currentDrafts[h.key]
    return d && d.score !== null && !scoreInRange(d.score, h.scale)
  })

  const doSubmit = async () => {
    if (!current || entries.length === 0 || outOfRange) return
    setError(null)
    const item = current
    try {
      await submit.mutateAsync({ id: run.id, scores: entries })
      safeSet(ANNOTATOR_KEY, annotator.trim())
      toast.success('已提交评分')
      if (mode === 'pending') {
        // 提交后列表里这一条会消失，下一条自然落在同一位置
        const stillPending = humans.some((h) => currentDrafts[h.key].score === null)
        if (stillPending) setIdx(safeIdx + 1)
        else setSubmitted((s) => new Set(s).add(item.id))
      } else {
        go(1)
      }
    } catch (e) {
      const f = formatApiError(e, '提交失败')
      setError([f.message, ...f.lines].join('\n'))
    }
  }

  // 键盘快捷键；在输入框 / 文本框里只响应 Ctrl+Enter
  const latest = useRef({ doSubmit, go, humans, activeKey, setDraft, currentDrafts })
  latest.current = { doSubmit, go, humans, activeKey, setDraft, currentDrafts }
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null
      const typing = !!t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT')
      const L = latest.current
      if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
        e.preventDefault()
        void L.doSubmit()
        return
      }
      if (typing || e.ctrlKey || e.metaKey || e.altKey) return
      if (e.key === 'j' || e.key === 'J') L.go(1)
      else if (e.key === 'k' || e.key === 'K') L.go(-1)
      else if (e.key === 'Enter') {
        // 焦点在别的按钮上时 Enter 是激活该按钮；打分按钮点过之后焦点还留在上面，这时 Enter 仍然是提交
        if (t?.tagName === 'BUTTON' && !t.closest('[data-score-group]')) return
        e.preventDefault()
        void L.doSubmit()
      } else {
        const cfg = L.humans.find((h) => h.key === L.activeKey)
        const score = cfg ? digitToScore(e.key, cfg.scale) : null
        if (cfg && score !== null) L.setDraft(cfg.key, { ...L.currentDrafts[cfg.key], score })
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  const prog = annotateProgress(run.stats, keys)
  const turns = detail.data?.case_detail?.input?.turns ?? []

  if (humans.length === 0) {
    return (
      <div className="space-y-2 p-6 text-sm" data-testid="no-human">
        <p>这次评测没有配置「人工评审」评估器。</p>
        <p className="text-xs text-muted-foreground">发起评测时添加「人工评审」评估器，或在评测报告里「重新评分」添加后再来打分。</p>
        <Link to={`/evals/${run.id}`} className="text-primary underline">
          返回评测报告
        </Link>
      </div>
    )
  }

  return (
    <div className="mx-auto max-w-7xl space-y-4 p-6" data-testid="annotate-view">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-lg font-semibold">人工评审</h1>
        <Link to={`/evals/${run.id}`} className="text-xs text-primary hover:underline">
          返回评测报告
        </Link>
        <div className="inline-flex rounded-md border border-border p-0.5" role="group" aria-label="评审范围">
          {(
            [
              ['pending', '待评审'],
              ['all', '全部（可修改）'],
            ] as const
          ).map(([m, label]) => (
            <button
              key={m}
              type="button"
              aria-pressed={mode === m}
              onClick={() => {
                setMode(m)
                setIdx(0)
              }}
              data-testid={`mode-${m}`}
              className={cn('rounded px-2.5 py-1 text-xs transition-colors', mode === m ? 'bg-primary text-primary-foreground' : 'text-muted-foreground hover:bg-accent')}
            >
              {label}
            </button>
          ))}
        </div>
        <div className="ml-auto flex items-center gap-2 text-xs text-muted-foreground" data-testid="annotate-progress">
          {prog ? (
            <>
              <div role="progressbar" aria-valuemin={0} aria-valuemax={prog.total} aria-valuenow={prog.done} className="h-1.5 w-40 overflow-hidden rounded-full bg-muted">
                <div className="h-full rounded-full bg-primary transition-all" style={{ width: `${prog.total ? (prog.done / prog.total) * 100 : 0}%` }} />
              </div>
              <span className="font-mono tabular-nums">
                已评 {prog.done}/{prog.total}，待评 {prog.pending}
              </span>
            </>
          ) : (
            <span>评测结束后显示进度</span>
          )}
        </div>
      </div>

      {query.isLoading ? (
        <LoadingBlock label="加载用例…" className="py-16" />
      ) : !current ? (
        <div className="rounded-md border border-dashed border-border py-16 text-center text-sm text-muted-foreground" data-testid="annotate-empty">
          {mode === 'pending' ? '没有待评审的用例了，辛苦！' : '没有可评审的用例。'}
        </div>
      ) : (
        <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_22rem]">
          <div className="min-w-0 space-y-4">
            <div className="flex items-center gap-2">
              <Button size="icon" variant="outline" aria-label="上一条（K）" onClick={() => go(-1)} disabled={safeIdx === 0}>
                <ChevronLeft className="h-4 w-4" />
              </Button>
              <Button size="icon" variant="outline" aria-label="下一条（J）" onClick={() => go(1)} disabled={safeIdx >= items.length - 1 && !hasNextPage}>
                <ChevronRight className="h-4 w-4" />
              </Button>
              <span className="font-mono text-sm" data-testid="annotate-case">
                {current.case_key}
                {run.repeats > 1 && <span className="text-muted-foreground"> #{current.repeat_idx + 1}</span>}
              </span>
              <span className="text-xs text-muted-foreground">
                第 {safeIdx + 1} 条{hasNextPage ? '+' : `/${items.length}`}
              </span>
              {current.tags.map((t) => (
                <Badge key={t} variant="secondary" className="font-mono">
                  {t}
                </Badge>
              ))}
              {current.session_id && (
                <Link to={`/sessions/${current.session_id}`} className="ml-auto inline-flex items-center gap-1 text-xs text-primary hover:underline">
                  在会话页打开
                  <ExternalLink className="h-3 w-3" />
                </Link>
              )}
            </div>

            {detail.isLoading && !detail.data ? (
              <LoadingBlock />
            ) : (
              <>
                <Block title={turns.length > 1 ? `输入（${turns.length} 轮）` : '输入'} testId="annotate-input">
                  {turns.length > 0 ? turns.join('\n\n') : (current.input_preview ?? '-')}
                </Block>
                <Block title="输出" testId="annotate-output">
                  <AnnotateOutput text={detail.data?.output ?? current.output_preview} />
                </Block>
                {current.expected && <Block title="期望">{current.expected}</Block>}
                {detail.data?.case_detail?.rubric && <Block title="评分标准 rubric">{detail.data.case_detail.rubric}</Block>}
                <section className="space-y-1.5">
                  <h3 className="text-xs font-semibold text-muted-foreground">执行轨迹（含工具调用）</h3>
                  <div className="h-[24rem] overflow-hidden rounded-md border border-border" data-testid="annotate-trace">
                    <ItemConversation sessionId={current.session_id} className="h-full" />
                  </div>
                </section>
              </>
            )}
          </div>

          <aside className="lg:sticky lg:top-4 lg:self-start">
            <Card>
              <CardHeader>
                <CardTitle>打分</CardTitle>
              </CardHeader>
              <CardContent className="space-y-3">
                {humans.map((h) => (
                  <ScoreInput
                    key={h.key}
                    cfg={h}
                    draft={currentDrafts[h.key] ?? { score: null, reason: '' }}
                    active={activeKey === h.key}
                    onFocus={() => setActiveKey(h.key)}
                    onChange={(d) => setDraft(h.key, d)}
                  />
                ))}
                <div className="space-y-1">
                  <label htmlFor="annotator" className="text-xs font-medium">
                    评审人
                  </label>
                  <Input id="annotator" placeholder="可选，记录在评分上" value={annotator} onChange={(e) => setAnnotator(e.target.value)} />
                </div>
                {error && (
                  <p role="alert" className="whitespace-pre-wrap text-xs text-danger">
                    {error}
                  </p>
                )}
                <Button className="w-full" onClick={() => void doSubmit()} loading={submit.isPending} disabled={entries.length === 0 || outOfRange} data-testid="submit-score">
                  提交{mode === 'pending' ? '并下一条' : ''}
                </Button>
                <p className="text-xs text-muted-foreground">快捷键：数字键打分 · J 下一条 · K 上一条 · Enter 提交 · 备注里 Ctrl+Enter 提交</p>
              </CardContent>
            </Card>
          </aside>
        </div>
      )}
    </div>
  )
}
