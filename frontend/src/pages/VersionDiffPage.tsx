import { useMemo, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { ArrowLeftRight, FileCode2, Undo2 } from 'lucide-react'
import { ApiError } from '@/api/client'
import { useRevision } from '@/api/queries/assets'
import { useAgent } from '@/api/queries/agents'
import { useVersion, useVersionDiff, useVersions } from '@/api/queries/versions'
import type { AssetKind, DiffOp, SpecChange, Version, VersionAssetRef } from '@/api/types'
import { Badge, type BadgeVariant } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { DiffEditor } from '@/components/ui/DiffEditor'
import { EmptyState } from '@/components/ui/EmptyState'
import { Select } from '@/components/ui/Select'
import { LoadingBlock } from '@/components/ui/Spinner'
import { ASSET_KIND_LABEL } from '@/lib/assetRef'
import { cn } from '@/lib/cn'
import { unpinSpec } from '@/lib/specDiff'
import { lineOfPath, specToYaml } from '@/lib/specYaml'
import { languageFor } from './assets/assetUtils'

export const OP_LABEL: Record<DiffOp, string> = {
  add: '新增',
  remove: '删除',
  replace: '修改',
  asset_changed: '资产变更',
}
export const OP_VARIANT: Record<DiffOp, BadgeVariant> = {
  add: 'success',
  remove: 'danger',
  replace: 'warning',
  asset_changed: 'info',
}

/** 变更路径所属的分组：取第一段（`tools[query-order]` -> `tools`） */
export function groupOfPath(path: string): string {
  return path.split(/[.[]/)[0] || path
}

/** 按分组归类并保持首次出现的顺序 */
export function groupChanges(changes: SpecChange[]): Array<{ group: string; items: SpecChange[] }> {
  const map = new Map<string, SpecChange[]>()
  for (const c of changes) {
    const g = groupOfPath(c.path)
    if (!map.has(g)) map.set(g, [])
    map.get(g)!.push(c)
  }
  return [...map.entries()].map(([group, items]) => ({ group, items }))
}

/** 从版本的资产清单里找出某个 asset_changed 路径对应的修订（`assets[].spec_path` 与 diff 的 path 同为身份键，直接相等） */
export function findAssetForPath(assets: VersionAssetRef[], path: string): VersionAssetRef | undefined {
  return assets.find((a) => a.spec_path === path)
}


/** 导航分组的显示名；没有登记的直接显示 Spec 的第一段路径 */
const GROUP_LABEL: Record<string, string> = { snapshot: '快照资产' }

export interface AssetChangeInfo {
  kind: AssetKind
  name: string
  /** 修订变化：修改为 `r3 → r4`，新增 / 删除为单个 `r1` */
  revs: string
}

function pinSummary(v: unknown): { kind: AssetKind; name: string; rev: number } | null {
  const p = v as { kind?: AssetKind; name?: string; rev?: number } | null | undefined
  return p && p.kind && p.name && p.rev !== undefined ? { kind: p.kind, name: p.name, rev: p.rev } : null
}

/** 资产类的变更按"类型 名称"显示（不显示 Spec 路径）；不是资产变更返回 null */
export function assetChangeInfo(c: SpecChange): AssetChangeInfo | null {
  if (c.op === 'asset_changed' && c.asset) {
    const l = (c.left as { rev?: number } | undefined)?.rev
    const r = (c.right as { rev?: number } | undefined)?.rev
    return { ...c.asset, revs: `r${l} → r${r}` }
  }
  if (c.op === 'add' || c.op === 'remove') {
    const p = pinSummary(c.op === 'add' ? c.right : c.left)
    return p && { kind: p.kind, name: p.name, revs: `r${p.rev}` }
  }
  return null
}

function preview(v: unknown): string {
  if (v === undefined) return ''
  const s = typeof v === 'string' ? v : JSON.stringify(v)
  return s.length > 28 ? `${s.slice(0, 28)}…` : s
}

export default function VersionDiffPage() {
  const { id = '' } = useParams()
  const [sp, setSp] = useSearchParams()
  const left = sp.get('left') ?? ''
  const right = sp.get('right') ?? ''
  const agent = useAgent(id)
  const versions = useVersions(id, 200)
  const list = versions.data?.pages.flatMap((p) => p.items) ?? []
  const diff = useVersionDiff(left, right)
  const leftId = diff.data?.left.id ?? left
  const rightId = diff.data?.right.id ?? right
  const lv = useVersion(diff.data?.left.id)
  const rv = useVersion(diff.data?.right.id)
  const [selected, setSelected] = useState<SpecChange | null>(null)
  const [reveal, setReveal] = useState<{ line: number; side: 'original' | 'modified'; key: number } | null>(null)
  const [assetView, setAssetView] = useState<{ change: SpecChange; l?: VersionAssetRef; r?: VersionAssetRef } | null>(null)

  const setSide = (side: 'left' | 'right', v: string) => {
    const next = new URLSearchParams(sp)
    next.set(side, v)
    setSp(next, { replace: true })
  }
  const swap = () => setSp({ left: right, right: left }, { replace: true })

  const yamls = useMemo(() => {
    const f = (v?: Version) => (v ? specToYaml(unpinSpec(v.spec)) : '')
    return { left: f(lv.data), right: f(rv.data) }
  }, [lv.data, rv.data])

  const onSelect = (c: SpecChange) => {
    setSelected(c)
    if (c.op === 'asset_changed' && lv.data && rv.data) {
      const l = findAssetForPath(lv.data.assets, c.path)
      const r = findAssetForPath(rv.data.assets, c.path)
      if (l && r) {
        setAssetView({ change: c, l, r })
        return
      }
    }
    setAssetView(null)
    const side = c.op === 'remove' ? 'original' : 'modified'
    const line = lineOfPath(side === 'original' ? yamls.left : yamls.right, c.path)
    if (line) setReveal({ line, side, key: Date.now() })
  }

  const groups = useMemo(() => groupChanges(diff.data?.changes ?? []), [diff.data])
  const summary = diff.data?.summary

  const versionSelect = (side: 'left' | 'right', value: string) => (
    <Select aria-label={side === 'left' ? '左侧版本' : '右侧版本'} value={value} onChange={(e) => setSide(side, e.target.value)} className="w-52">
      {!list.some((v) => v.id === value) && <option value={value}>{value ? value.slice(-10) : '选择版本…'}</option>}
      {list.map((v) => (
        <option key={v.id} value={v.id}>
          v{v.seq}
          {v.tags.length ? ` · ${v.tags.join(',')}` : ''}
          {v.notes ? ` · ${v.notes.slice(0, 16)}` : ''}
        </option>
      ))}
    </Select>
  )

  return (
    <div className="flex h-full min-h-0 flex-col">
      {/* 顶部：版本选择 + summary */}
      <div className="flex shrink-0 flex-wrap items-center gap-3 border-b border-border px-4 py-2.5">
        <Link to={`/agents/${id}?tab=versions`} className="text-sm font-semibold hover:underline">
          {agent.data?.name ?? id}
        </Link>
        <span className="text-muted-foreground">/</span>
        {versionSelect('left', leftId)}
        <Button variant="ghost" size="icon" aria-label="交换左右" onClick={swap}>
          <ArrowLeftRight className="h-4 w-4" />
        </Button>
        {versionSelect('right', rightId)}
        {summary && (
          <div className="ml-auto flex items-center gap-1.5 text-xs" data-testid="diff-summary">
            {(Object.keys(OP_LABEL) as DiffOp[]).map((op) => (
              <Badge key={op} variant={summary[op] ? OP_VARIANT[op] : 'secondary'}>
                {OP_LABEL[op]} {summary[op] ?? 0}
              </Badge>
            ))}
          </div>
        )}
      </div>

      {!left || !right ? (
        <EmptyState title="请选择两个版本" description="在版本列表中勾选两个版本，或在上方选择器中选择。" />
      ) : diff.isLoading ? (
        <LoadingBlock />
      ) : diff.error ? (
        <p className="p-6 text-sm text-danger">无法对比：{diff.error instanceof ApiError ? diff.error.message : String(diff.error)}</p>
      ) : (
        <div className="grid min-h-0 flex-1 grid-cols-[18rem_minmax(0,1fr)]">
          {/* 变更导航 */}
          <nav aria-label="变更导航" className="min-h-0 overflow-y-auto border-r border-border bg-muted/20 p-2">
            <button
              type="button"
              onClick={() => {
                setSelected(null)
                setAssetView(null)
              }}
              className={cn(
                'mb-2 flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm hover:bg-accent',
                !assetView && 'font-medium',
              )}
            >
              <FileCode2 className="h-4 w-4 text-muted-foreground" />
              配置（YAML 全文）
            </button>
            {groups.length === 0 && <p className="px-2 py-4 text-center text-xs text-muted-foreground">两个版本的内容完全相同</p>}
            {groups.map((g) => (
              <div key={g.group} className="mb-2">
                <p className="px-2 pb-0.5 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                  {GROUP_LABEL[g.group] ?? g.group} <span className="font-normal">({g.items.length})</span>
                </p>
                <ul>
                  {g.items.map((c) => {
                    const asset = assetChangeInfo(c)
                    return (
                      <li key={`${c.path}-${c.op}`}>
                        <button
                          type="button"
                          data-testid={`change-${c.path}`}
                          onClick={() => onSelect(c)}
                          className={cn(
                            'flex w-full flex-col gap-0.5 rounded-md px-2 py-1 text-left hover:bg-accent',
                            selected === c && 'bg-accent',
                          )}
                        >
                          <span className="flex items-center gap-1.5">
                            <Badge variant={OP_VARIANT[c.op]}>{OP_LABEL[c.op]}</Badge>
                            <span className="min-w-0 truncate font-mono text-xs" title={c.path}>
                              {asset ? `${ASSET_KIND_LABEL[asset.kind]} ${asset.name}` : c.path}
                            </span>
                          </span>
                          {c.op === 'replace' && c.content_diff === undefined && (
                            <span className="truncate pl-1 font-mono text-[11px] text-muted-foreground">
                              {preview(c.left)} → {preview(c.right)}
                            </span>
                          )}
                          {asset && <span className="pl-1 font-mono text-[11px] text-muted-foreground">{asset.revs}</span>}
                        </button>
                      </li>
                    )
                  })}
                </ul>
              </div>
            ))}
          </nav>

          {/* 右侧 DiffEditor */}
          <div className="flex min-h-0 min-w-0 flex-col">
            {assetView ? (
              <AssetContentDiff view={assetView} onBack={() => setAssetView(null)} />
            ) : lv.isLoading || rv.isLoading ? (
              <LoadingBlock />
            ) : (
              <>
                <div className="flex shrink-0 items-center justify-between border-b border-border px-3 py-1.5 text-xs text-muted-foreground">
                  <span>
                    配置视图 · v{diff.data?.left.seq} → v{diff.data?.right.seq}（资产引用显示为 asset://类型/名称@修订）
                  </span>
                </div>
                <div className="min-h-0 flex-1">
                  <DiffEditor
                    original={yamls.left}
                    modified={yamls.right}
                    language="yaml"
                    height="100%"
                    className="rounded-none border-0"
                    reveal={reveal}
                  />
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

function AssetContentDiff({
  view,
  onBack,
}: {
  view: { change: SpecChange; l?: VersionAssetRef; r?: VersionAssetRef }
  onBack: () => void
}) {
  const l = useRevision(view.l?.asset_revision_id)
  const r = useRevision(view.r?.asset_revision_id)
  return (
    <>
      <div className="flex shrink-0 items-center gap-3 border-b border-border px-3 py-1.5 text-xs">
        <Button size="sm" variant="ghost" onClick={onBack}>
          <Undo2 className="h-3.5 w-3.5" />
          返回配置视图
        </Button>
        <span className="font-mono">
          {view.l && ASSET_KIND_LABEL[view.l.kind]} {view.l?.name}
        </span>
        <span className="text-muted-foreground">
          r{view.l?.rev} → r{view.r?.rev}
        </span>
        {view.l && (
          <span className="ml-auto text-muted-foreground">
            <Badge variant="outline">{view.change.path}</Badge>
          </span>
        )}
      </div>
      <div className="min-h-0 flex-1">
        {l.isLoading || r.isLoading ? (
          <LoadingBlock />
        ) : !l.data?.content_available || !r.data?.content_available ? (
          <EmptyState title="内容不可预览" description="资产内容过大或不是 UTF-8 文本。" />
        ) : (
          <DiffEditor
            original={l.data.content ?? ''}
            modified={r.data.content ?? ''}
            language={languageFor(view.l?.kind ?? 'file', r.data.content_type)}
            height="100%"
            className="rounded-none border-0"
          />
        )}
      </div>
    </>
  )
}
