import { useBindingState, type BindingSnapshot } from '@/api/queries/adapters'
import { Badge } from '@/components/ui/Badge'

const SOURCE_LABEL: Record<string, string> = {
  spec: '版本规范内置',
  override: '覆盖',
  legacy: '旧数据（沿用版本规范内置接入）',
}

/** 会话固定的适配器接入：「HiAgent 发布版本 · 接入修订 N」，悬停显示该修订的来源与说明。 */
export function AdapterBadge({ versionId, snapshot }: { versionId: string; snapshot: BindingSnapshot }) {
  const state = useBindingState(versionId)
  const binding = snapshot.binding!
  const revision = state.data?.history.find((h) => h.revision === snapshot.revision)
  const { source } = snapshot
  const published = binding.config.published_version
  const name = binding.id === 'hiagent' ? 'HiAgent' : binding.id
  const title = [
    `接入修订 ${snapshot.revision}`,
    `来源：${(source && SOURCE_LABEL[source]) ?? source ?? '未知'}`,
    `说明：${revision?.reason ?? '无'}`,
  ].join('\n')
  return (
    <Badge variant="outline" title={title} data-testid="adapter-badge">
      {name} 发布版本 {String(published)} · 接入修订 {snapshot.revision}
    </Badge>
  )
}
