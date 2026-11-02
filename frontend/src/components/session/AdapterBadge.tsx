import type { BindingSnapshot } from '@/api/queries/adapters'
import { Badge } from '@/components/ui/Badge'
import { snapshotLabel, snapshotTitle } from '@/lib/adapterEnvironment'

/** 会话 / 评测所用的适配器环境：「生产 · 存档 #N」，悬停显示适配器、关键配置和存档说明。 */
export function AdapterBadge({ snapshot }: { snapshot: BindingSnapshot }) {
  const fellBack = snapshot.requested_environment && snapshot.requested_environment !== snapshot.environment
  return (
    <Badge variant="outline" title={snapshotTitle(snapshot)} data-testid="adapter-badge">
      {snapshotLabel(snapshot)}{fellBack ? '（测试未生效）' : ''}
    </Badge>
  )
}
