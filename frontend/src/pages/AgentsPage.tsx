import { PagePlaceholder } from '@/components/PagePlaceholder'

/** 占位页：实现时直接替换本文件内容（路由已在 src/routes.tsx 注册） */
export default function AgentsPage() {
  return (
    <PagePlaceholder
      title="Agents"
      spec="详设 14.1（/agents 为列表入口，14.1 未单列）"
      planned={[
        'Agent 列表、新建 Agent（config / code）',
        '归档与搜索',
      ]}
    />
  )
}
