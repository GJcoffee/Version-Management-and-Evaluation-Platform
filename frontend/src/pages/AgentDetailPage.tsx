import { Link, useParams, useSearchParams } from 'react-router-dom'
import { ApiError } from '@/api/client'
import { useAgent } from '@/api/queries/agents'
import { useVersions } from '@/api/queries/versions'
import { Badge } from '@/components/ui/Badge'
import { LoadingBlock } from '@/components/ui/Spinner'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/Tabs'
import { DraftsTab } from './agent/DraftsTab'
import { EvalsTab } from './agent/EvalsTab'
import { SessionsTab } from './agent/SessionsTab'
import { VersionsTab } from './agent/VersionsTab'
import { KIND_LABEL } from './AgentsPage'

const TABS = ['drafts', 'versions', 'sessions', 'evals'] as const
type TabKey = (typeof TABS)[number]

export default function AgentDetailPage() {
  const { id } = useParams()
  const [sp, setSp] = useSearchParams()
  const agent = useAgent(id)
  const versions = useVersions(id)
  const tabParam = sp.get('tab')
  const tab: TabKey = (TABS as readonly string[]).includes(tabParam ?? '') ? (tabParam as TabKey) : 'drafts'

  if (agent.isLoading) return <LoadingBlock className="py-24" />
  if (agent.error || !agent.data) {
    return (
      <div className="p-6 text-sm text-danger">
        无法加载 Agent：{agent.error instanceof ApiError ? agent.error.message : '未知错误'}
        <Link to="/agents" className="ml-3 text-primary underline">
          返回列表
        </Link>
      </div>
    )
  }
  const a = agent.data
  const list = versions.data?.pages.flatMap((p) => p.items) ?? []

  return (
    <div className="mx-auto max-w-6xl space-y-4 p-6">
      <div>
        <div className="flex flex-wrap items-center gap-2">
          <h1 className="text-lg font-semibold">{a.name}</h1>
          <Badge variant={a.kind === 'config' ? 'info' : 'warning'}>{KIND_LABEL[a.kind]}</Badge>
          {a.archived && <Badge variant="secondary">已归档</Badge>}
          {a.latest_version ? (
            <Badge variant="outline" className="font-mono">
              最新 v{a.latest_version.seq}
            </Badge>
          ) : (
            <Badge variant="outline">未发布</Badge>
          )}
        </div>
        {a.description && <p className="mt-1 text-sm text-muted-foreground">{a.description}</p>}
      </div>

      <Tabs value={tab} onValueChange={(v) => setSp({ tab: v }, { replace: true })}>
        <TabsList>
          <TabsTrigger value="drafts">草稿</TabsTrigger>
          <TabsTrigger value="versions" badge={list.length ? <span className="text-xs text-muted-foreground">{list.length}{versions.hasNextPage ? '+' : ''}</span> : undefined}>
            版本
          </TabsTrigger>
          <TabsTrigger value="sessions">会话</TabsTrigger>
          <TabsTrigger value="evals">评测</TabsTrigger>
        </TabsList>
        <TabsContent value="drafts" className="pt-4">
          <DraftsTab agent={a} versions={list} />
        </TabsContent>
        <TabsContent value="versions" className="pt-4">
          <VersionsTab
            agent={a}
            versions={list}
            hasMore={!!versions.hasNextPage}
            loadingMore={versions.isFetchingNextPage}
            onLoadMore={() => void versions.fetchNextPage()}
          />
        </TabsContent>
        <TabsContent value="sessions" className="pt-4">
          <SessionsTab agentId={a.id} />
        </TabsContent>
        <TabsContent value="evals" className="pt-4">
          <EvalsTab agentId={a.id} />
        </TabsContent>
      </Tabs>
    </div>
  )
}
