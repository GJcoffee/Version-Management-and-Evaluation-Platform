import { PagePlaceholder } from '@/components/PagePlaceholder'

/** 占位页：实现时直接替换本文件内容（路由已在 src/routes.tsx 注册） */
export default function ComparePage() {
  return (
    <PagePlaceholder
      title="对比"
      spec="详设 9 / 14.2 DualCompare"
      planned={[
        '双栏对话（live / history / replay 共用）',
        '步骤对齐视图与字符级文本 diff',
      ]}
    />
  )
}
