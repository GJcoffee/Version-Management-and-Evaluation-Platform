import { Link } from 'react-router-dom'
import { ApiError } from '@/api/client'
import { useAssetReferences } from '@/api/queries/assets'
import { Badge } from '@/components/ui/Badge'
import { LoadingBlock } from '@/components/ui/Spinner'

/** "被引用"面板：哪些版本、哪些评测、哪些草稿用到了该资产 */
export function ReferencesPanel({ assetId }: { assetId: string }) {
  const refs = useAssetReferences(assetId)
  if (refs.isLoading) return <LoadingBlock />
  if (refs.error) return <p className="py-6 text-sm text-danger">加载失败：{refs.error instanceof ApiError ? refs.error.message : String(refs.error)}</p>
  const r = refs.data
  if (!r) return null
  const empty = r.versions.length + r.evals.length + r.drafts.length === 0
  return (
    <div className="space-y-5" data-testid="references">
      <div className="flex items-center gap-2 text-sm">
        {r.in_use ? <Badge variant="warning">被版本或评测引用，不能硬删除</Badge> : <Badge variant="secondary">未被版本或评测引用</Badge>}
      </div>
      {empty && <p className="text-sm text-muted-foreground">还没有任何版本、评测或草稿引用这个资产。</p>}

      {r.versions.length > 0 && (
        <section>
          <h3 className="mb-1.5 text-sm font-semibold">版本（{r.versions.length}）</h3>
          <ul className="divide-y divide-border rounded-md border border-border text-sm">
            {r.versions.map((v) => (
              <li key={`${v.version_id}-${v.spec_path}`} className="flex items-center gap-3 px-3 py-1.5">
                <Link to={`/agents/${v.agent_id}?tab=versions`} className="font-medium hover:underline">
                  {v.agent_name}
                </Link>
                <span className="font-mono text-xs">v{v.seq}</span>
                <Badge variant="outline">r{v.rev}</Badge>
                <span className="ml-auto font-mono text-xs text-muted-foreground">{v.spec_path}</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {r.evals.length > 0 && (
        <section>
          <h3 className="mb-1.5 text-sm font-semibold">评测（{r.evals.length}）</h3>
          <ul className="divide-y divide-border rounded-md border border-border text-sm">
            {r.evals.map((e) => (
              <li key={e.run_id} className="flex items-center gap-3 px-3 py-1.5">
                <Link to={`/evals/${e.run_id}`} className="font-mono text-xs hover:underline">
                  {e.run_id}
                </Link>
                <Badge variant="secondary">{e.status}</Badge>
                <Badge variant="outline">数据集 r{e.rev}</Badge>
              </li>
            ))}
          </ul>
        </section>
      )}

      {r.drafts.length > 0 && (
        <section>
          <h3 className="mb-1.5 text-sm font-semibold">草稿（{r.drafts.length}，不阻止删除）</h3>
          <ul className="divide-y divide-border rounded-md border border-border text-sm">
            {r.drafts.map((d) => (
              <li key={d.draft_id} className="flex items-center gap-3 px-3 py-1.5">
                <Link to={`/agents/${d.agent_id}/draft/${d.draft_id}`} className="font-medium hover:underline">
                  {d.agent_name} / {d.draft_name}
                </Link>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  )
}
