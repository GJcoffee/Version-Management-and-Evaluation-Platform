import { useState } from 'react'
import { ApiError } from '@/api/client'
import { useAssetDiff, useRevision } from '@/api/queries/assets'
import type { AssetKind, DatasetAssetDiff, DatasetCaseBrief } from '@/api/types'
import { Badge } from '@/components/ui/Badge'
import { DiffEditor } from '@/components/ui/DiffEditor'
import { EmptyState } from '@/components/ui/EmptyState'
import { LoadingBlock } from '@/components/ui/Spinner'
import { formatBytes } from '@/lib/format'
import { languageFor } from './assetUtils'

/** 两个修订的对比：文本类用 Monaco DiffEditor，数据集显示增删改明细 */
export function RevisionDiff({ kind, leftId, rightId }: { kind: AssetKind; leftId: string; rightId: string }) {
  const diff = useAssetDiff(leftId, rightId)
  if (diff.isLoading) return <LoadingBlock />
  if (diff.error) return <p className="py-6 text-sm text-danger">无法对比：{diff.error instanceof ApiError ? diff.error.message : String(diff.error)}</p>
  const d = diff.data
  if (!d) return null
  if (d.type === 'dataset') return <DatasetDiffView d={d} />
  if (d.type === 'binary') {
    return (
      <EmptyState title="二进制内容，无法逐行对比" description={`左侧 ${formatBytes(d.sizes.left)}，右侧 ${formatBytes(d.sizes.right)}`} />
    )
  }
  return <TextDiffView kind={kind} leftId={leftId} rightId={rightId} identical={d.identical} />
}

function TextDiffView({ kind, leftId, rightId, identical }: { kind: AssetKind; leftId: string; rightId: string; identical: boolean }) {
  const l = useRevision(leftId)
  const r = useRevision(rightId)
  if (l.isLoading || r.isLoading) return <LoadingBlock />
  if (!l.data?.content_available || !r.data?.content_available) {
    return <EmptyState title="内容不可预览" description="内容过大或不是 UTF-8 文本，请下载后对比。" />
  }
  return (
    <div className="space-y-2">
      {identical && <p className="text-xs text-muted-foreground">两个修订的内容完全相同。</p>}
      <DiffEditor
        original={l.data.content ?? ''}
        modified={r.data.content ?? ''}
        language={languageFor(kind, r.data.content_type)}
        height={460}
      />
    </div>
  )
}

function CaseTable({ rows }: { rows: DatasetCaseBrief[] }) {
  return (
    <ul className="divide-y divide-border rounded-md border border-border text-sm">
      {rows.map((c) => (
        <li key={c.case_key} className="flex items-start gap-3 px-3 py-1.5">
          <span className="w-40 shrink-0 truncate font-mono text-xs" title={c.case_key}>
            {c.case_key}
          </span>
          <span className="min-w-0 flex-1 truncate" title={c.input.turns.join('\n')}>
            {c.input.turns.join(' ⏎ ')}
          </span>
          <span className="flex shrink-0 gap-1">
            {c.tags.map((t) => (
              <Badge key={t} variant="secondary" className="font-mono">
                {t}
              </Badge>
            ))}
          </span>
        </li>
      ))}
    </ul>
  )
}

function DatasetDiffView({ d }: { d: DatasetAssetDiff }) {
  const [tab, setTab] = useState<'added' | 'removed' | 'modified'>(d.summary.modified ? 'modified' : d.summary.added ? 'added' : 'removed')
  const s = d.summary
  const tabs = [
    { k: 'added' as const, label: '新增', n: s.added, v: 'success' as const },
    { k: 'removed' as const, label: '删除', n: s.removed, v: 'danger' as const },
    { k: 'modified' as const, label: '修改', n: s.modified, v: 'warning' as const },
  ]
  return (
    <div className="space-y-3" data-testid="dataset-diff">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="text-muted-foreground">
          {s.left_count} → {s.right_count} 个用例，未变化 {s.unchanged}
        </span>
        {d.truncated && <Badge variant="warning">明细每类最多显示 500 条</Badge>}
      </div>
      <div className="flex gap-2">
        {tabs.map((t) => (
          <button
            key={t.k}
            type="button"
            onClick={() => setTab(t.k)}
            className={`flex items-center gap-1.5 rounded-md border px-3 py-1 text-sm ${tab === t.k ? 'border-primary bg-primary/5' : 'border-border hover:bg-accent'}`}
          >
            {t.label}
            <Badge variant={t.n ? t.v : 'secondary'}>{t.n}</Badge>
          </button>
        ))}
      </div>
      {tab === 'added' && (d.added.length ? <CaseTable rows={d.added} /> : <p className="py-6 text-center text-sm text-muted-foreground">没有新增用例</p>)}
      {tab === 'removed' && (d.removed.length ? <CaseTable rows={d.removed} /> : <p className="py-6 text-center text-sm text-muted-foreground">没有删除的用例</p>)}
      {tab === 'modified' &&
        (d.modified.length ? (
          <ul className="space-y-2">
            {d.modified.map((m) => (
              <li key={m.case_key} className="rounded-md border border-border p-3">
                <p className="mb-2 flex items-center gap-2">
                  <span className="font-mono text-sm font-medium">{m.case_key}</span>
                  {m.changed_fields.map((f) => (
                    <Badge key={f} variant="warning">
                      {f}
                    </Badge>
                  ))}
                </p>
                <div className="grid grid-cols-2 gap-3 text-xs">
                  <pre className="max-h-40 overflow-auto whitespace-pre-wrap rounded bg-danger/10 p-2 font-mono">{JSON.stringify(m.left, null, 2)}</pre>
                  <pre className="max-h-40 overflow-auto whitespace-pre-wrap rounded bg-success/10 p-2 font-mono">{JSON.stringify(m.right, null, 2)}</pre>
                </div>
              </li>
            ))}
          </ul>
        ) : (
          <p className="py-6 text-center text-sm text-muted-foreground">没有修改的用例</p>
        ))}
    </div>
  )
}
