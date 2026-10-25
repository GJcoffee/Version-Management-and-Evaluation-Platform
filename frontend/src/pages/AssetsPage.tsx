import { PagePlaceholder } from '@/components/PagePlaceholder'

/** 占位页：实现时直接替换本文件内容（路由已在 src/routes.tsx 注册） */
export default function AssetsPage() {
  return (
    <PagePlaceholder
      title="资产库"
      spec="详设 5 / 14.1 /assets"
      planned={[
        '按 kind 筛选的资产列表、新建资产、数据集导入',
      ]}
    />
  )
}
