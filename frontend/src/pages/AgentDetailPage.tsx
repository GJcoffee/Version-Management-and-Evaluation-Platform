import { PagePlaceholder } from '@/components/PagePlaceholder'

/** 占位页：实现时直接替换本文件内容（路由已在 src/routes.tsx 注册） */
export default function AgentDetailPage() {
  return (
    <PagePlaceholder
      title="Agent 详情"
      spec="详设 14.1 /agents/:id"
      planned={[
        '标签页：草稿 / 版本 / 会话 / 评测',
        '版本标签（tag）与回滚',
      ]}
    />
  )
}
