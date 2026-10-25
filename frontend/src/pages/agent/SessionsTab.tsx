import { MessagesSquare } from 'lucide-react'
import { EmptyState } from '@/components/ui/EmptyState'

/** 会话标签页：下一阶段由会话模块的开发者替换本组件（props 里有 agentId） */
export function SessionsTab(_props: { agentId: string }) {
  return <EmptyState icon={MessagesSquare} title="下一阶段提供" description="这里将列出该 Agent 的会话，并支持发起新对话。" />
}
