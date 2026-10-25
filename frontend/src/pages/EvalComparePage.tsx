import { PagePlaceholder } from '@/components/PagePlaceholder'

/** 占位页：实现时直接替换本文件内容（路由已在 src/routes.tsx 注册） */
export default function EvalComparePage() {
  return (
    <PagePlaceholder
      title="评测对比"
      spec="详设 8.7 / 14.2 EvalCompare"
      planned={[
        '结论卡片（Δ、置信区间、胜平负、p 值）',
        '分类页签：退化 / 改善 / 不稳定 / 新增 / 缺失',
      ]}
    />
  )
}
