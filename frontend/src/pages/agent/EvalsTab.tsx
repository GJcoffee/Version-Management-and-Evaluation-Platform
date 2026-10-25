import { FlaskConical } from 'lucide-react'
import { EmptyState } from '@/components/ui/EmptyState'

/** 评测标签页：下一阶段由评测模块的开发者替换本组件（props 里有 agentId） */
export function EvalsTab(_props: { agentId: string }) {
  return <EmptyState icon={FlaskConical} title="下一阶段提供" description="这里将列出该 Agent 的评测运行，并支持发起评测。" />
}
