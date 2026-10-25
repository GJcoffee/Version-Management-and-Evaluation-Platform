import { PagePlaceholder } from '@/components/PagePlaceholder'

/** 占位页：实现时直接替换本文件内容（路由已在 src/routes.tsx 注册） */
export default function VersionDiffPage() {
  return (
    <PagePlaceholder
      title="版本对比"
      spec="详设 4.5 / 14.2 DiffView"
      planned={[
        '左侧变更导航（按 path 分组）',
        '右侧 Monaco DiffEditor 展示两侧完整文本',
      ]}
    />
  )
}
