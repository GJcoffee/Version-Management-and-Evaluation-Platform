import { PagePlaceholder } from '@/components/PagePlaceholder'

/** 占位页：实现时直接替换本文件内容（路由已在 src/routes.tsx 注册） */
export default function SessionPage() {
  return (
    <PagePlaceholder
      title="会话"
      spec="详设 14.2 ChatPane / TraceViewer"
      planned={[
        '对话流（处理 llm.delta）+ 工具调用卡片',
        'Trace 树、时间瀑布图、指标条（tokens / 耗时 / TTFT / 成本）',
      ]}
    />
  )
}
