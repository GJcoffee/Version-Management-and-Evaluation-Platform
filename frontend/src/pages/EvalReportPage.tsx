import { PagePlaceholder } from '@/components/PagePlaceholder'

/** 占位页：实现时直接替换本文件内容（路由已在 src/routes.tsx 注册） */
export default function EvalReportPage() {
  return (
    <PagePlaceholder
      title="评测报告"
      spec="详设 14.2 EvalReport"
      planned={[
        '指标卡片、评估器分数分布（ECharts）、按标签的通过率',
        '用例表（虚拟滚动）与详情抽屉',
      ]}
    />
  )
}
