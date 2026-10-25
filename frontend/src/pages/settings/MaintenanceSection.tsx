import { useState } from 'react'
import { ApiError } from '@/api/client'
import { useGc } from '@/api/queries/system'
import type { GcResult } from '@/api/types'
import { Button } from '@/components/ui/Button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/Card'
import { formatBytes } from '@/lib/format'
import { toast } from '@/stores/toast'

export function MaintenanceSection() {
  const gc = useGc()
  const [result, setResult] = useState<GcResult | null>(null)

  const run = async () => {
    try {
      const r = await gc.mutateAsync()
      setResult(r)
      toast.success('数据清理完成', { description: `删除 ${r.blobs_deleted} 个对象，释放 ${formatBytes(r.bytes_freed)}` })
    } catch (e) {
      toast.error('数据清理失败', { description: e instanceof ApiError ? e.message : undefined })
    }
  }

  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle>数据清理</CardTitle>
          <CardDescription>
            删除无引用且创建超过 24 小时的内容对象（blob），并清理已软删除且无人引用的资产修订。
          </CardDescription>
        </div>
      </CardHeader>
      <CardContent className="flex flex-wrap items-center gap-4">
        <Button variant="outline" onClick={run} loading={gc.isPending}>
          立即清理
        </Button>
        {result && (
          <p className="text-sm" role="status">
            已删除 <span className="font-mono font-medium">{result.blobs_deleted}</span> 个对象，释放{' '}
            <span className="font-mono font-medium">{formatBytes(result.bytes_freed)}</span>
          </p>
        )}
      </CardContent>
    </Card>
  )
}
