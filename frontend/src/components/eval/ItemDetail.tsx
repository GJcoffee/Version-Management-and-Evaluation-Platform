import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { ExternalLink } from 'lucide-react'
import type { EvalItemDetailOut, EvalScoreOut } from '@/api/evalTypes'
import { ChatPane, useSessionStream } from '@/components/session'
import { Badge } from '@/components/ui/Badge'
import { formatCost, formatDuration } from '@/lib/format'
import { VERDICT_LABEL, formatScore, itemErrorText, itemVerdict, scoreTone } from './evalUtils'

export function VerdictBadge({ item }: { item: Pick<EvalItemDetailOut, 'status' | 'passed'> }) {
  const v = itemVerdict(item)
  const variant = v === 'passed' ? 'success' : v === 'not_passed' ? 'danger' : v === 'failed' ? 'warning' : 'secondary'
  return (
    <Badge variant={variant} data-testid="item-verdict" data-verdict={v}>
      {v === 'pending' && item.status === 'pending' ? '排队中' : VERDICT_LABEL[v]}
    </Badge>
  )
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="space-y-1.5">
      <h3 className="text-xs font-semibold text-muted-foreground">{title}</h3>
      {children}
    </section>
  )
}

function Text({ children }: { children: ReactNode }) {
  return <div className="whitespace-pre-wrap rounded-md bg-muted/50 px-3 py-2 text-sm">{children}</div>
}

export function ScoreList({ scores }: { scores: EvalScoreOut[] }) {
  if (scores.length === 0) return <p className="text-xs text-muted-foreground">暂无评分</p>
  return (
    <ul className="space-y-2" data-testid="score-list">
      {scores.map((s) => (
        <li key={s.evaluator_key} className="rounded-md border border-border px-3 py-2" data-testid={`score-${s.evaluator_key}`}>
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-mono text-xs font-medium">{s.evaluator_key}</span>
            {s.evaluator_type && <span className="text-xs text-muted-foreground">{s.evaluator_type}</span>}
            {s.gate === false && <Badge variant="outline">不参与判定</Badge>}
            <span className="ml-auto flex items-center gap-2">
              {s.status === 'error' ? (
                <Badge variant="warning">评分出错</Badge>
              ) : (
                <>
                  <span className="font-mono text-sm tabular-nums">{formatScore(s.score)}</span>
                  {s.raw_score !== null && s.raw_score !== s.score && <span className="font-mono text-xs text-muted-foreground">原始 {s.raw_score}</span>}
                  <Badge variant={scoreTone(s.passed)}>{s.passed === true ? '通过' : s.passed === false ? '未通过' : '-'}</Badge>
                </>
              )}
            </span>
          </div>
          {s.reason && <p className="mt-1.5 whitespace-pre-wrap text-xs text-muted-foreground">{s.reason}</p>}
        </li>
      ))}
    </ul>
  )
}

/** 一次执行的完整对话（复用 ChatPane + 会话 SSE），会话回放结束后即为完整内容 */
export function ItemConversation({ sessionId, className }: { sessionId: string | null | undefined; className?: string }) {
  const stream = useSessionStream(sessionId ?? undefined)
  if (!sessionId) return <p className="p-4 text-center text-xs text-muted-foreground">这次执行没有会话（可能尚未开始或已取消）</p>
  return (
    <ChatPane
      className={className}
      turns={stream.turns}
      sessionId={sessionId}
      loading={stream.loading}
      autoScroll={false}
      emptyState={<p className="p-6 text-center text-xs text-muted-foreground">会话没有内容</p>}
    />
  )
}

/** 抽屉内容：输入 / 期望 / 评分 / 指标 / 对话 */
export function ItemDetailBody({ item, showConversation = true }: { item: EvalItemDetailOut; showConversation?: boolean }) {
  const turns = item.case_detail?.input?.turns ?? []
  const err = itemErrorText(item.error)
  return (
    <div className="space-y-4 p-4" data-testid="item-detail">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <VerdictBadge item={item} />
        {item.tags.map((t) => (
          <Badge key={t} variant="secondary" className="font-mono">
            {t}
          </Badge>
        ))}
        {item.cached && <Badge variant="outline">缓存命中</Badge>}
        <span className="ml-auto flex items-center gap-3 font-mono text-muted-foreground">
          <span>{formatDuration(item.latency_ms)}</span>
          <span>{formatCost(item.cost)}</span>
          {item.attempts > 1 && <span>尝试 {item.attempts} 次</span>}
        </span>
      </div>
      {err && <p className="rounded-md border border-danger/40 bg-danger/10 px-3 py-2 text-xs text-danger">{err}</p>}
      <Section title={turns.length > 1 ? `输入（${turns.length} 轮）` : '输入'}>
        {turns.length === 0 ? (
          <Text>{item.input_preview ?? '-'}</Text>
        ) : (
          <div className="space-y-1.5">
            {turns.map((t, i) => (
              <Text key={i}>{t}</Text>
            ))}
          </div>
        )}
      </Section>
      <Section title="期望">
        <Text>{item.expected ?? <span className="text-muted-foreground">（用例没有 expected）</span>}</Text>
      </Section>
      {item.case_detail?.rubric && (
        <Section title="评分标准 rubric">
          <Text>{item.case_detail.rubric}</Text>
        </Section>
      )}
      <Section title="评估器分数与理由">
        <ScoreList scores={item.scores} />
      </Section>
      {showConversation && (
        <Section title="完整对话">
          <div className="flex items-center justify-between">
            <span className="text-xs text-muted-foreground">本次执行的会话</span>
            {item.session_id && (
              <Link to={`/sessions/${item.session_id}`} className="inline-flex items-center gap-1 text-xs text-primary hover:underline" data-testid="open-session">
                在会话页打开
                <ExternalLink className="h-3 w-3" />
              </Link>
            )}
          </div>
          <div className="h-[28rem] overflow-hidden rounded-md border border-border" data-testid="item-conversation">
            <ItemConversation sessionId={item.session_id} className="h-full" />
          </div>
        </Section>
      )}
    </div>
  )
}
