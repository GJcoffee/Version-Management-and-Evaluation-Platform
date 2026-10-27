import { useState } from 'react'
import { Link } from 'react-router-dom'
import { FilePen, GitCompare, Library } from 'lucide-react'
import { ApiError } from '@/api/client'
import { useVersionAssets } from '@/api/queries/versions'
import type { Agent, VersionSummary } from '@/api/types'
import { AssetIdentity } from '@/components/AssetIdentity'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { EmptyState } from '@/components/ui/EmptyState'
import { Select } from '@/components/ui/Select'
import { LoadingBlock } from '@/components/ui/Spinner'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/Table'
import { ASSET_KIND_LABEL, SNAPSHOT_READONLY_NOTE, assetTitle } from '@/lib/assetRef'
import { changeText, groupVersionAssets } from './assetGroups'

/**
 * Agent 绑定的资产：所选版本（默认最新）引用的资产按类型分组，
 * 每项显示所用修订、最近一次变化发生在哪个版本；点击进入资产详情（内容、修订对比、引用关系）。
 */
export function AssetsTab({
  agent,
  versions,
  onEditDraft,
}: {
  agent: Agent
  versions: VersionSummary[]
  onEditDraft: () => void
}) {
  const [picked, setPicked] = useState<string | null>(null)
  const version = versions.find((v) => v.id === picked) ?? versions[0]
  const assets = useVersionAssets(version?.id)

  if (!version) {
    return (
      <EmptyState
        icon={Library}
        title="还没有发布过版本"
        description="版本发布后，它引用的资产（提示词、Skill、知识库等）会列在这里。"
        action={
          agent.kind === 'config' ? (
            <Button size="sm" variant="outline" onClick={onEditDraft}>
              <FilePen className="h-3.5 w-3.5" />
              去草稿编辑
            </Button>
          ) : undefined
        }
      />
    )
  }

  const groups = groupVersionAssets(assets.data?.items ?? [])
  const previous = versions.find((v) => v.seq === version.seq - 1)
  const hasReadonly = (assets.data?.items ?? []).some((i) => i.origin === 'snapshot')

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <label className="flex items-center gap-2 text-sm">
          <span className="text-xs text-muted-foreground">版本</span>
          <Select aria-label="选择版本" className="w-56" value={version.id} onChange={(e) => setPicked(e.target.value)}>
            {versions.map((v) => (
              <option key={v.id} value={v.id}>
                v{v.seq}
                {v === versions[0] ? ' · 最新' : ''}
                {v.tags.length ? ` · ${v.tags.join(',')}` : ''}
              </option>
            ))}
          </Select>
        </label>
        {previous && (
          <Link to={`/agents/${agent.id}/versions/diff?left=${previous.id}&right=${version.id}`}>
            <Button size="sm" variant="ghost">
              <GitCompare className="h-3.5 w-3.5" />
              与 v{previous.seq} 的差异
            </Button>
          </Link>
        )}
        {agent.kind === 'config' && (
          <Button size="sm" variant="outline" className="ml-auto" onClick={onEditDraft}>
            <FilePen className="h-3.5 w-3.5" />
            在草稿中编辑
          </Button>
        )}
      </div>
      {hasReadonly && <p className="text-xs text-muted-foreground">带“只读”标记的资产：{SNAPSHOT_READONLY_NOTE}。</p>}

      {assets.isLoading ? (
        <LoadingBlock />
      ) : assets.error ? (
        <p className="py-6 text-sm text-danger">加载失败：{assets.error instanceof ApiError ? assets.error.message : String(assets.error)}</p>
      ) : groups.length === 0 ? (
        <EmptyState
          icon={Library}
          title={`v${version.seq} 没有绑定资产`}
          description={agent.kind === 'config' ? '提示词写成内联文本时不会产生资产；引用资产后会列在这里。' : undefined}
        />
      ) : (
        groups.map(({ group, items }) => (
          <section key={group.key} aria-label={group.label} data-testid={`asset-group-${group.key}`}>
            <h3 className="mb-1.5 text-sm font-semibold">
              {group.label} <span className="font-normal text-muted-foreground">({items.length})</span>
            </h3>
            <Table>
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead>名称</TableHead>
                  <TableHead className="w-24">所用修订</TableHead>
                  <TableHead className="w-56">最近一次变化</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {items.map((i) => (
                  <TableRow key={i.asset_id}>
                    <TableCell>
                      <Link to={`/assets/${i.asset_id}?rev=${i.rev}`} className="font-mono text-sm font-medium hover:underline">
                        {assetTitle(i)}
                      </Link>
                      <AssetIdentity asset={i} />
                      {group.kinds.length > 1 && (
                        <Badge variant="outline" className="ml-2">
                          {ASSET_KIND_LABEL[i.kind]}
                        </Badge>
                      )}
                      {i.origin === 'snapshot' && (
                        <Badge variant="secondary" className="ml-2" title={SNAPSHOT_READONLY_NOTE}>
                          只读
                        </Badge>
                      )}
                    </TableCell>
                    <TableCell className="font-mono text-xs">r{i.rev}</TableCell>
                    <TableCell className="text-xs">
                      <span className="font-mono font-semibold">v{i.changed_in_seq}</span>
                      <span className="ml-2 font-mono text-muted-foreground">{changeText(i)}</span>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </section>
        ))
      )}
    </div>
  )
}
