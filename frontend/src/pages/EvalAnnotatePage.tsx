import { PagePlaceholder } from '@/components/PagePlaceholder'

/** 占位页：实现时直接替换本文件内容（路由已在 src/routes.tsx 注册） */
export default function EvalAnnotatePage() {
  return (
    <PagePlaceholder
      title="人工评审"
      spec="详设 14.2 AnnotateView"
      planned={[
        '键盘快捷键（数字键打分，J/K 切换）',
        '评审标准常驻显示',
      ]}
    />
  )
}
