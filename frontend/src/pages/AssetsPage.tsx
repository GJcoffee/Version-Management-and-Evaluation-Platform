import { useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { Library, Plus, Search } from 'lucide-react'
import { ApiError } from '@/api/client'
import { useAssets } from '@/api/queries/assets'
import type { Asset, AssetKind } from '@/api/types'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { EmptyState } from '@/components/ui/EmptyState'
import { Input } from '@/components/ui/Input'
import { RelativeTime } from '@/components/ui/RelativeTime'
import { LoadingBlock } from '@/components/ui/Spinner'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/Table'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/Tabs'
import { ASSET_KINDS, ASSET_KIND_LABEL, isSnapshotAsset } from '@/lib/assetRef'
import { formatBytes } from '@/lib/format'
import { useDebouncedValue } from '@/lib/useDebouncedValue'
import { CreateAssetDialog } from './assets/CreateAssetDialog'
import { DatasetImportDialog } from './assets/DatasetImportDialog'

function describe(a: Asset): string | null {
  const m = a.latest?.meta ?? {}
  if (a.kind === 'dataset' && typeof m.case_count === 'number') return `${m.case_count} 个用例`
  if (a.kind === 'tool' && typeof m.tool_name === 'string') return `工具名 ${m.tool_name}`
  if (a.kind === 'prompt' && Array.isArray(m.variables) && m.variables.length) return `变量 ${(m.variables as string[]).join(', ')}`
  return null
}

export default function AssetsPage() {
  const [sp, setSp] = useSearchParams()
  const kindParam = sp.get('kind')
  const kind: AssetKind = (ASSET_KINDS as string[]).includes(kindParam ?? '') ? (kindParam as AssetKind) : 'prompt'
  const [q, setQ] = useState('')
  const [showDeleted, setShowDeleted] = useState(false)
  const [createOpen, setCreateOpen] = useState(false)
  const [importOpen, setImportOpen] = useState(false)
  const dq = useDebouncedValue(q.trim(), 250)
  const list = useAssets({ kind, q: dq, include_deleted: showDeleted })
  const assets = list.data?.pages.flatMap((p) => p.items) ?? []

  const openNew = () => (kind === 'dataset' ? setImportOpen(true) : setCreateOpen(true))

  return (
    <div className="mx-auto max-w-6xl space-y-4 p-6">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold">资产库</h1>
          <p className="text-xs text-muted-foreground">
            提示词、工具、知识库、Skill、MCP 插件、数据库、应用配置、数据集和文件。每次修改产生不可变的新修订，Agent 通过 @latest 或固定修订引用。
          </p>
        </div>
        <Button onClick={openNew}>
          <Plus className="h-4 w-4" />
          {kind === 'dataset' ? '导入数据集' : '新建资产'}
        </Button>
      </div>

      <Tabs value={kind} onValueChange={(v) => setSp({ kind: v }, { replace: true })}>
        <TabsList className="flex-wrap">
          {ASSET_KINDS.map((k) => (
            <TabsTrigger key={k} value={k}>
              {ASSET_KIND_LABEL[k]}
            </TabsTrigger>
          ))}
        </TabsList>
      </Tabs>

      <div className="flex flex-wrap items-center gap-3">
        <div className="relative w-64">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input className="pl-8" placeholder="搜索名称或描述…" aria-label="搜索资产" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        <label className="flex cursor-pointer items-center gap-2 text-sm">
          <input type="checkbox" className="h-4 w-4 accent-primary" checked={showDeleted} onChange={(e) => setShowDeleted(e.target.checked)} />
          显示已删除
        </label>
      </div>

      {list.isLoading ? (
        <LoadingBlock />
      ) : list.error ? (
        <div className="flex items-center gap-3 py-6 text-sm text-danger">
          加载失败：{list.error instanceof ApiError ? list.error.message : String(list.error)}
          <Button size="sm" variant="outline" onClick={() => list.refetch()}>
            重试
          </Button>
        </div>
      ) : assets.length === 0 ? (
        <EmptyState
          icon={Library}
          title={dq ? `没有匹配“${dq}”的${ASSET_KIND_LABEL[kind]}` : `还没有${ASSET_KIND_LABEL[kind]}`}
          action={
            !dq ? (
              <Button size="sm" onClick={openNew}>
                <Plus className="h-3.5 w-3.5" />
                {kind === 'dataset' ? '导入数据集' : '新建资产'}
              </Button>
            ) : undefined
          }
        />
      ) : (
        <>
          <Table>
            <TableHeader>
              <TableRow className="hover:bg-transparent">
                <TableHead>名称</TableHead>
                <TableHead>当前修订</TableHead>
                <TableHead>摘要</TableHead>
                <TableHead>大小</TableHead>
                <TableHead>最近修订</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {assets.map((a) => (
                <TableRow key={a.id} className={a.deleted_at ? 'opacity-60' : undefined}>
                  <TableCell>
                    <Link to={`/assets/${a.id}`} className="font-mono text-sm font-medium hover:underline">
                      {a.name}
                    </Link>
                    {a.deleted_at && (
                      <Badge variant="danger" className="ml-2">
                        已删除
                      </Badge>
                    )}
                    {isSnapshotAsset(a) && (
                      <Badge variant="secondary" className="ml-2" title="来自 agent 快照（只读），请在源平台修改后重新发布">
                        只读
                      </Badge>
                    )}
                    {a.description && <p className="mt-0.5 max-w-md truncate text-xs text-muted-foreground">{a.description}</p>}
                  </TableCell>
                  <TableCell className="font-mono text-xs">r{a.latest_rev}</TableCell>
                  <TableCell className="text-xs text-muted-foreground">{describe(a) ?? '-'}</TableCell>
                  <TableCell className="text-xs text-muted-foreground">{a.latest ? formatBytes(a.latest.size) : '-'}</TableCell>
                  <TableCell className="text-xs text-muted-foreground">
                    <RelativeTime value={a.latest?.created_at ?? a.created_at} />
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          {list.hasNextPage && (
            <div className="flex justify-center">
              <Button variant="outline" size="sm" loading={list.isFetchingNextPage} onClick={() => void list.fetchNextPage()}>
                加载更多
              </Button>
            </div>
          )}
        </>
      )}

      <CreateAssetDialog open={createOpen} onOpenChange={setCreateOpen} defaultKind={kind} onDataset={() => setImportOpen(true)} />
      <DatasetImportDialog open={importOpen} onOpenChange={setImportOpen} />
    </div>
  )
}
