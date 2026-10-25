import { Link } from 'react-router-dom'
import { SearchX } from 'lucide-react'
import { EmptyState } from '@/components/ui/EmptyState'
import { Button } from '@/components/ui/Button'

export default function NotFoundPage() {
  return (
    <EmptyState
      icon={SearchX}
      title="页面不存在"
      description="请检查地址是否正确。"
      className="py-24"
      action={
        <Link to="/">
          <Button variant="outline">返回首页</Button>
        </Link>
      }
    />
  )
}
