import { useMemo, useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { Download, FilePlus2, GitCompare, RotateCcw, Trash2 } from 'lucide-react'
import { ApiError } from '@/api/client'
import { useAsset, useAssetRevisions, useDeleteAsset, useRestoreAsset, useRevision } from '@/api/queries/assets'
import type { Asset, AssetRevision } from '@/api/types'
import { AssetIdentity } from '@/components/AssetIdentity'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { CodeEditor } from '@/components/ui/CodeEditor'
import { ConfirmDialog } from '@/components/ui/ConfirmDialog'
import { Dialog } from '@/components/ui/Dialog'
import { EmptyState } from '@/components/ui/EmptyState'
import { IdText } from '@/components/ui/IdText'
import { RelativeTime } from '@/components/ui/RelativeTime'
import { Select } from '@/components/ui/Select'
import { LoadingBlock } from '@/components/ui/Spinner'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/Tabs'
import { ASSET_KIND_LABEL, SNAPSHOT_READONLY_NOTE, assetTitle, isSnapshotAsset } from '@/lib/assetRef'
import { cn } from '@/lib/cn'
import { formatBytes } from '@/lib/format'
import { toast } from '@/stores/toast'
import { languageFor } from './assets/assetUtils'
import { DatasetCases } from './assets/DatasetCases'
import { DatasetImportDialog } from './assets/DatasetImportDialog'
import { NewRevisionDialog } from './assets/NewRevisionDialog'
import { ReferencesPanel } from './assets/ReferencesPanel'
import { RevisionDiff } from './assets/RevisionDiff'

export default function AssetDetailPage() {
  const { id } = useParams()
  const asset = useAsset(id)
  if (asset.isLoading) return <LoadingBlock className="py-24" />
  if (asset.error || !asset.data) {
    return (
      <div className="p-6 text-sm text-danger">
        无法加载资产：{asset.error instanceof ApiError ? asset.error.message : '未知错误'}
        <Link to="/assets" className="ml-3 text-primary underline">
          返回资产库
        </Link>
      </div>
    )
  }
  return <AssetDetail asset={asset.data} />
}

function AssetDetail({ asset }: { asset: Asset }) {
  const navigate = useNavigate()
  const revs = useAssetRevisions(asset.id, 100)
  const revisions = useMemo(() => revs.data?.pages.flatMap((p) => p.items) ?? [], [revs.data])
  const [viewId, setViewId] = useState<string | null>(null)
  const [tab, setTab] = useState('content')
  const [pair, setPair] = useState<{ left: string | null; right: string | null }>({ left: null, right: null })
  const [newRevOpen, setNewRevOpen] = useState(false)
  const [importOpen, setImportOpen] = useState(false)
  const [softOpen, setSoftOpen] = useState(false)
  const [hardOpen, setHardOpen] = useState(false)
  const del = useDeleteAsset()
  const restore = useRestoreAsset()

  // `?rev=N`：从 agent 的资产列表进来时，默认查看该版本用的修订
  const [sp] = useSearchParams()
  const linkedRev = revisions.find((r) => r.rev === Number(sp.get('rev')))
  const activeId =
    viewId ?? linkedRev?.id ?? asset.latest?.id ?? revisions.find((r) => r.rev === asset.latest_rev)?.id ?? revisions[0]?.id ?? null
  const active = revisions.find((r) => r.id === activeId) ?? asset.latest
  const isDataset = asset.kind === 'dataset'
  const content = useRevision(isDataset ? null : activeId)
  const deleted = !!asset.deleted_at
  const readonly = isSnapshotAsset(asset)

  const toggleCompare = (rev: AssetRevision) => {
    const cur = [pair.left, pair.right].filter((x): x is string => !!x)
    const next = cur.includes(rev.id) ? cur.filter((x) => x !== rev.id) : [...cur, rev.id].slice(-2)
    const byRev = (id: string) => revisions.find((r) => r.id === id)?.rev ?? 0
    next.sort((a, b) => byRev(a) - byRev(b))
    setPair({ left: next[0] ?? null, right: next[1] ?? null })
  }
  const selectedCount = [pair.left, pair.right].filter(Boolean).length

  const onRestore = async () => {
    try {
      await restore.mutateAsync(asset.id)
      toast.success('已恢复')
    } catch (e) {
      toast.error('恢复失败', { description: e instanceof ApiError ? e.message : undefined })
    }
  }

  return (
    <div className="mx-auto max-w-7xl space-y-4 p-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="font-mono text-lg font-semibold">{assetTitle(asset)}</h1>
            <Badge variant="info">{ASSET_KIND_LABEL[asset.kind]}</Badge>
            <Badge variant="outline" className="font-mono">
              当前 r{asset.latest_rev}
            </Badge>
            {deleted && <Badge variant="danger">已删除</Badge>}
            {readonly && <Badge variant="secondary">只读</Badge>}
          </div>
          <p className="mt-0.5">
            <AssetIdentity asset={asset} className="" />
          </p>
          {asset.former_names.length > 0 && (
            <p className="mt-1 text-xs text-muted-foreground" data-testid="former-names">
              曾用名：<span className="font-mono">{asset.former_names.join('、')}</span>（改名后修订历史保留）
            </p>
          )}
          {asset.description && <p className="mt-1 text-sm text-muted-foreground">{asset.description}</p>}
          {readonly && (
            <p className="mt-1 text-sm text-muted-foreground" data-testid="readonly-note">
              {SNAPSHOT_READONLY_NOTE}
            </p>
          )}
        </div>
        {!readonly && (
          <div className="flex flex-wrap items-center gap-2">
            {deleted ? (
              <Button variant="outline" size="sm" onClick={onRestore} loading={restore.isPending}>
                <RotateCcw className="h-3.5 w-3.5" />
                恢复
              </Button>
            ) : (
              <Button size="sm" onClick={() => (isDataset ? setImportOpen(true) : setNewRevOpen(true))}>
                <FilePlus2 className="h-3.5 w-3.5" />
                {isDataset ? '导入新修订' : '新建修订'}
              </Button>
            )}
            {!deleted && (
              <Button variant="outline" size="sm" onClick={() => setSoftOpen(true)}>
                <Trash2 className="h-3.5 w-3.5" />
                删除
              </Button>
            )}
            <Button variant="ghost" size="sm" className="text-danger" onClick={() => setHardOpen(true)}>
              彻底删除
            </Button>
          </div>
        )}
      </div>

      {deleted && (
        <div className="rounded-md border border-warning/40 bg-warning/10 px-3 py-2 text-sm">
          该资产已被软删除：列表默认不显示，已有版本仍可读取其内容。恢复后才能追加修订。
        </div>
      )}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[19rem_minmax(0,1fr)]">
        {/* 修订历史 */}
        <aside className="space-y-2" aria-label="修订历史">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-semibold">修订历史</h2>
            <Button
              size="sm"
              variant="outline"
              disabled={selectedCount !== 2}
              onClick={() => setTab('diff')}
              title="勾选两个修订后对比"
            >
              <GitCompare className="h-3.5 w-3.5" />
              对比 {selectedCount}/2
            </Button>
          </div>
          {revs.isLoading ? (
            <LoadingBlock />
          ) : (
            <ul className="divide-y divide-border rounded-md border border-border" data-testid="revision-list">
              {revisions.map((r) => (
                <li key={r.id} className={cn('flex items-start gap-2 px-2.5 py-2', r.id === activeId && 'bg-accent')}>
                  <input
                    type="checkbox"
                    aria-label={`选择 r${r.rev} 用于对比`}
                    className="mt-1 h-4 w-4 accent-primary"
                    checked={pair.left === r.id || pair.right === r.id}
                    onChange={() => toggleCompare(r)}
                  />
                  <button
                    type="button"
                    className="min-w-0 flex-1 text-left"
                    onClick={() => {
                      setViewId(r.id)
                      setTab('content')
                    }}
                  >
                    <span className="flex items-center gap-1.5">
                      <span className="font-mono text-sm font-semibold">r{r.rev}</span>
                      {r.rev === asset.latest_rev && <Badge variant="success">当前</Badge>}
                      <span className="ml-auto text-xs text-muted-foreground">{formatBytes(r.size)}</span>
                    </span>
                    <span className="mt-0.5 block truncate text-xs" title={r.message ?? undefined}>
                      {r.message ?? <span className="text-muted-foreground">（无提交说明）</span>}
                    </span>
                    <span className="block text-[11px] text-muted-foreground">
                      <RelativeTime value={r.created_at} />
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
          {revs.hasNextPage && (
            <Button variant="outline" size="sm" className="w-full" loading={revs.isFetchingNextPage} onClick={() => void revs.fetchNextPage()}>
              加载更多
            </Button>
          )}
        </aside>

        {/* 主区域 */}
        <section className="min-w-0">
          <Tabs value={tab} onValueChange={setTab}>
            <TabsList>
              <TabsTrigger value="content">内容{active ? ` · r${active.rev}` : ''}</TabsTrigger>
              <TabsTrigger value="diff">修订对比</TabsTrigger>
              <TabsTrigger value="refs">被引用</TabsTrigger>
            </TabsList>

            <TabsContent value="content" className="pt-4">
              {!active ? (
                <EmptyState title="没有修订" />
              ) : (
                <div className="space-y-3">
                  <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
                    <span>{active.content_type}</span>
                    <span>{formatBytes(active.size)}</span>
                    <span>
                      hash <IdText value={active.blob_hash} />
                    </span>
                    {Object.entries(active.meta)
                      .filter(([k, v]) => k !== 'schema_warnings' && k !== 'column_map' && v !== null && typeof v !== 'object')
                      .map(([k, v]) => (
                        <span key={k}>
                          {k}: <span className="font-mono text-foreground">{String(v)}</span>
                        </span>
                      ))}
                    {Array.isArray(active.meta.variables) && active.meta.variables.length > 0 && (
                      <span>
                        变量: <span className="font-mono text-foreground">{(active.meta.variables as string[]).join(', ')}</span>
                      </span>
                    )}
                    {active.message && <span className="text-foreground">“{active.message}”</span>}
                  </div>
                  {Array.isArray(active.meta.schema_warnings) && active.meta.schema_warnings.length > 0 && (
                    <div className="rounded-md border border-warning/40 bg-warning/10 p-2 text-xs">
                      导入时有 {active.meta.schema_warnings.length} 条警告：
                      <ul className="mt-1 list-inside list-disc">
                        {active.meta.schema_warnings.slice(0, 5).map((w, i) => (
                          <li key={i}>{typeof w === 'string' ? w : ((w as { message?: string }).message ?? JSON.stringify(w))}</li>
                        ))}
                      </ul>
                    </div>
                  )}
                  {isDataset ? (
                    <DatasetCases key={active.id} revId={active.id} total={active.meta.case_count} />
                  ) : content.isLoading ? (
                    <LoadingBlock />
                  ) : content.data?.content_available ? (
                    <CodeEditor
                      value={content.data.content ?? ''}
                      readOnly
                      language={languageFor(asset.kind, active.content_type)}
                      height={440}
                      path={`view-${active.id}`}
                    />
                  ) : (
                    <EmptyState
                      title="内容不可预览"
                      description="内容过大或不是 UTF-8 文本。"
                      action={
                        <a href={`/api/v1/assets/revisions/${active.id}/raw`} download>
                          <Button size="sm" variant="outline">
                            <Download className="h-3.5 w-3.5" />
                            下载原文件
                          </Button>
                        </a>
                      }
                    />
                  )}
                </div>
              )}
            </TabsContent>

            <TabsContent value="diff" className="space-y-3 pt-4">
              <div className="flex flex-wrap items-center gap-3 text-sm">
                <RevSelect label="左侧" value={pair.left} revisions={revisions} onChange={(v) => setPair((p) => ({ ...p, left: v }))} />
                <span className="text-muted-foreground">→</span>
                <RevSelect label="右侧" value={pair.right} revisions={revisions} onChange={(v) => setPair((p) => ({ ...p, right: v }))} />
                {revisions.length >= 2 && (
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => setPair({ left: revisions[1].id, right: revisions[0].id })}
                  >
                    最近两个修订
                  </Button>
                )}
              </div>
              {pair.left && pair.right ? (
                <RevisionDiff key={`${pair.left}-${pair.right}`} kind={asset.kind} leftId={pair.left} rightId={pair.right} />
              ) : (
                <EmptyState title="选择两个修订" description="在左侧列表勾选两个修订，或在上方下拉框中选择。" />
              )}
            </TabsContent>

            <TabsContent value="refs" className="pt-4">
              <ReferencesPanel assetId={asset.id} />
            </TabsContent>
          </Tabs>
        </section>
      </div>

      {newRevOpen && (
        <NewRevisionDialog
          asset={asset}
          baseContent={content.data?.content_available ? (content.data.content ?? '') : null}
          baseContentType={content.data?.content_type}
          onClose={() => setNewRevOpen(false)}
          onCreated={(rid) => {
            setViewId(rid)
            setTab('content')
          }}
        />
      )}
      <DatasetImportDialog
        open={importOpen}
        onOpenChange={setImportOpen}
        fixedName={asset.name}
        onImported={(r) => {
          setViewId(r.revision.id)
          setTab('content')
        }}
      />

      <ConfirmDialog
        open={softOpen}
        onOpenChange={setSoftOpen}
        title={`删除资产「${asset.name}」？`}
        description="软删除：列表不再显示，已有版本和评测仍可读取其内容，之后可以恢复。"
        confirmText="删除"
        onConfirm={async () => {
          try {
            await del.mutateAsync({ id: asset.id })
            toast.success('已删除（可恢复）')
          } catch (e) {
            toast.error('删除失败', { description: e instanceof ApiError ? e.message : undefined })
            throw e
          }
        }}
      />
      {hardOpen && (
        <HardDeleteDialog
          asset={asset}
          onClose={() => setHardOpen(false)}
          onDeleted={() => {
            toast.success(`已彻底删除 ${asset.name}`)
            navigate(`/assets?kind=${asset.kind}`)
          }}
        />
      )}
    </div>
  )
}

function RevSelect({
  label,
  value,
  revisions,
  onChange,
}: {
  label: string
  value: string | null
  revisions: AssetRevision[]
  onChange: (id: string | null) => void
}) {
  return (
    <label className="flex items-center gap-2">
      <span className="text-xs text-muted-foreground">{label}</span>
      <Select aria-label={`${label}修订`} className="w-44" value={value ?? ''} onChange={(e) => onChange(e.target.value || null)}>
        <option value="">选择修订…</option>
        {revisions.map((r) => (
          <option key={r.id} value={r.id}>
            r{r.rev}
            {r.message ? ` · ${r.message.slice(0, 14)}` : ''}
          </option>
        ))}
      </Select>
    </label>
  )
}

/** 硬删除：被引用时后端返回 409 ASSET_IN_USE，把返回的错误信息原样展示在对话框里 */
function HardDeleteDialog({ asset, onClose, onDeleted }: { asset: Asset; onClose: () => void; onDeleted: () => void }) {
  const del = useDeleteAsset()
  const [error, setError] = useState<string | null>(null)
  const run = async () => {
    setError(null)
    try {
      await del.mutateAsync({ id: asset.id, hard: true })
      onClose()
      onDeleted()
    } catch (e) {
      setError(e instanceof ApiError ? e.message : '删除失败')
    }
  }
  return (
    <Dialog
      open
      onOpenChange={(v) => !v && onClose()}
      size="sm"
      title={`彻底删除「${asset.name}」？`}
      footer={
        <>
          <Button variant="outline" onClick={onClose}>
            取消
          </Button>
          <Button variant="danger" onClick={run} loading={del.isPending}>
            彻底删除
          </Button>
        </>
      }
    >
      <p className="text-sm text-muted-foreground">将物理删除该资产及其全部修订，不可恢复。被版本或评测引用的资产无法彻底删除，只能软删除。</p>
      {error && (
        <div role="alert" data-testid="hard-delete-error" className="mt-3 rounded-md border border-danger/40 bg-danger/10 p-2.5 text-sm text-danger">
          {error}
        </div>
      )}
    </Dialog>
  )
}
