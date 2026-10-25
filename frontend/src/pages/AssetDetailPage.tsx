import { PagePlaceholder } from '@/components/PagePlaceholder'

/** 占位页：实现时直接替换本文件内容（路由已在 src/routes.tsx 注册） */
export default function AssetDetailPage() {
  return (
    <PagePlaceholder
      title="资产详情"
      spec="详设 5.4 / 14.1 /assets/:id"
      planned={[
        '修订历史与修订 diff',
        '被哪些版本引用',
      ]}
    />
  )
}
