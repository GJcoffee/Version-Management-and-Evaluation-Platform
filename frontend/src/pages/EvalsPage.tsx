import { PagePlaceholder } from '@/components/PagePlaceholder'

/** 占位页：实现时直接替换本文件内容（路由已在 src/routes.tsx 注册） */
export default function EvalsPage() {
  return (
    <PagePlaceholder
      title="评测"
      spec="详设 8 / 17（W6 评测列表页）"
      planned={[
        '评测批次与 Run 列表、发起评测、进度显示',
      ]}
    />
  )
}
