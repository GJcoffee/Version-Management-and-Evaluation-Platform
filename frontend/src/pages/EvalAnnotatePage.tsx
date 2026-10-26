import { Link, useParams } from 'react-router-dom'
import { ApiError } from '@/api/client'
import { useEval } from '@/api/queries/evals'
import { AnnotateView } from '@/components/eval/AnnotateView'
import { LoadingBlock } from '@/components/ui/Spinner'

/** 人工评审页（详设 14.2 AnnotateView）：路由 /evals/:id/annotate，入口在评测报告页 */
export default function EvalAnnotatePage() {
  const { id } = useParams()
  const run = useEval(id)
  if (run.isLoading) return <LoadingBlock label="加载评测…" className="py-24" />
  if (run.error || !run.data) {
    const notFound = run.error instanceof ApiError && run.error.status === 404
    return (
      <div className="p-6 text-sm text-danger">
        {notFound ? '评测不存在' : `无法加载评测：${run.error instanceof ApiError ? run.error.message : '未知错误'}`}
        <Link to="/evals" className="ml-3 text-primary underline">
          返回评测列表
        </Link>
      </div>
    )
  }
  return <AnnotateView key={run.data.id} run={run.data} />
}
