import { PagePlaceholder } from '@/components/PagePlaceholder'

/** 占位页：实现时直接替换本文件内容（路由已在 src/routes.tsx 注册） */
export default function DraftEditorPage() {
  return (
    <PagePlaceholder
      title="草稿编辑器"
      spec="详设 14.2 DraftEditor"
      planned={[
        '左侧表单（由 AgentSpec JSON Schema 生成）+ 右侧 Monaco YAML，双向同步',
        '防抖 validate，错误标注到对应行',
        '资产引用选择器、发布按钮与相对基础版本的改动数',
      ]}
    />
  )
}
