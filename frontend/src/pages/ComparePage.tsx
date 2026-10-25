import { Link, useParams } from 'react-router-dom'
import { ApiError } from '@/api/client'
import { useCompare } from '@/api/queries/compare'
import { LoadingBlock } from '@/components/ui/Spinner'
import { CompareView } from './compare/CompareView'

/** 双栏对比页（`/compare/:id`）：live / history / replay 三种模式共用，见 compare/CompareView */
export default function ComparePage() {
  const { id } = useParams()
  const group = useCompare(id)
  if (group.isLoading) return <LoadingBlock label="加载对比…" className="py-24" />
  if (group.error || !group.data) {
    const notFound = group.error instanceof ApiError && group.error.status === 404
    return (
      <div className="p-6 text-sm text-danger">
        {notFound ? '对比不存在' : `无法加载对比：${group.error instanceof ApiError ? group.error.message : '未知错误'}`}
        <Link to="/agents" className="ml-3 text-primary underline">
          返回 Agents
        </Link>
      </div>
    )
  }
  return <CompareView key={group.data.id} group={group.data} />
}
