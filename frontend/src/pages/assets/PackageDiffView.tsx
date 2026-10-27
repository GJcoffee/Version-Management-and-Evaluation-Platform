import { useState } from 'react'
import { FileText } from 'lucide-react'
import { useSkillFile } from '@/api/queries/assets'
import type { PackageDiff } from '@/api/types'
import { Badge } from '@/components/ui/Badge'
import { DiffEditor } from '@/components/ui/DiffEditor'
import { EmptyState } from '@/components/ui/EmptyState'
import { LoadingBlock } from '@/components/ui/Spinner'
import { cn } from '@/lib/cn'
import { languageForPath } from './assetUtils'

type Tab = 'added' | 'removed' | 'modified'

const join = (root: string, path: string) => (root ? `${root}/${path}` : path)

function FileDiff({ diff, path, leftRevisionId, rightRevisionId }: { diff: PackageDiff; path: string; leftRevisionId: string; rightRevisionId: string }) {
  const l = useSkillFile(leftRevisionId, join(diff.left_root, path))
  const r = useSkillFile(rightRevisionId, join(diff.right_root, path))
  if (l.isLoading || r.isLoading) return <LoadingBlock />
  if (!l.data?.content_available || !r.data?.content_available) {
    return <EmptyState title="内容不可预览" description="文件加载失败，或不是 UTF-8 文本。" />
  }
  return (
    <DiffEditor
      original={l.data.content ?? ''}
      modified={r.data.content ?? ''}
      language={languageForPath(path)}
      height="100%"
      className="rounded-none border-0"
    />
  )
}

/** Skill 文件包的逐文件变化：新增 / 删除 / 修改的文件列表，点击修改的文件查看该文件的 diff */
export function PackageDiffView({ diff, leftRevisionId, rightRevisionId }: { diff: PackageDiff; leftRevisionId: string; rightRevisionId: string }) {
  const [tab, setTab] = useState<Tab>(diff.modified.length ? 'modified' : diff.added.length ? 'added' : 'removed')
  const [picked, setPicked] = useState<string | null>(null)
  const tabs = [
    { k: 'added' as const, label: '新增', n: diff.added.length, v: 'success' as const },
    { k: 'removed' as const, label: '删除', n: diff.removed.length, v: 'danger' as const },
    { k: 'modified' as const, label: '修改', n: diff.modified.length, v: 'warning' as const },
  ]
  const paths = tab === 'modified' ? diff.modified.map((m) => m.path) : diff[tab]
  const selected = tab === 'modified' ? (picked ?? diff.modified[0]?.path ?? null) : null
  const item = diff.modified.find((m) => m.path === selected)
  return (
    <div className="flex h-full min-h-0 flex-col" data-testid="package-diff">
      <div className="flex flex-wrap items-center gap-2 border-b border-border px-3 py-2">
        {tabs.map((t) => (
          <button
            key={t.k}
            type="button"
            aria-pressed={tab === t.k}
            onClick={() => setTab(t.k)}
            className={cn('flex items-center gap-1.5 rounded-md border px-3 py-1 text-sm', tab === t.k ? 'border-primary bg-primary/5' : 'border-border hover:bg-accent')}
          >
            {t.label}
            <Badge variant={t.n ? t.v : 'secondary'}>{t.n}</Badge>
          </button>
        ))}
        <span className="text-xs text-muted-foreground">未变化 {diff.unchanged_count} 个文件</span>
      </div>
      <div className="grid min-h-0 flex-1 grid-cols-1 md:grid-cols-[18rem_minmax(0,1fr)]">
        <ul aria-label="文件列表" className="max-h-60 min-h-0 space-y-0.5 overflow-auto border-b border-border bg-muted/20 p-2 md:max-h-none md:border-b-0 md:border-r">
          {paths.length === 0 && <li className="px-2 py-4 text-center text-xs text-muted-foreground">没有{tabs.find((t) => t.k === tab)?.label}的文件</li>}
          {paths.map((path) => (
            <li key={path}>
              {tab === 'modified' ? (
                <button
                  type="button"
                  aria-pressed={selected === path}
                  title={path}
                  onClick={() => setPicked(path)}
                  className={cn('flex w-full items-center gap-1.5 rounded px-1.5 py-1.5 text-left text-xs', selected === path ? 'bg-primary/10 text-primary' : 'hover:bg-accent')}
                >
                  <FileText className="h-3.5 w-3.5 shrink-0" />
                  <span className="break-all font-mono">{path}</span>
                </button>
              ) : (
                <span title={path} className="flex items-center gap-1.5 px-1.5 py-1.5 text-xs">
                  <FileText className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                  <span className="break-all font-mono">{path}</span>
                </span>
              )}
            </li>
          ))}
        </ul>
        <div className="min-h-[320px] min-w-0">
          {item ? (
            item.content_diff ? (
              <FileDiff key={item.path} diff={diff} path={item.path} leftRevisionId={leftRevisionId} rightRevisionId={rightRevisionId} />
            ) : (
              <EmptyState title="二进制文件，无法逐行对比" description="文件内容有变化，但不是 UTF-8 文本或超过预览限制，请到资产详情下载后对比。" />
            )
          ) : (
            <p className="p-6 text-center text-sm text-muted-foreground">{tab === 'modified' ? '没有修改的文件' : '点击「修改」里的文件查看差异'}</p>
          )}
        </div>
      </div>
    </div>
  )
}
