import { useEffect, useRef, useState } from 'react'
import { Download } from 'lucide-react'
import type { EvalRunStatusOut } from '@/api/evalTypes'
import { Button } from '@/components/ui/Button'
import { Dialog } from '@/components/ui/Dialog'
import { formatApiError } from '@/lib/apiError'
import { toast } from '@/stores/toast'
import { downloadEvalExport, fetchEvalExport, type EvalExportFormat } from './evalExport'
import { isRunActive } from './evalUtils'

export function EvalExportButton({ runId, status }: { runId: string; status: EvalRunStatusOut }) {
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState<EvalExportFormat | null>(null)
  const [progress, setProgress] = useState({ loaded: 0, total: 0 })
  const controller = useRef<AbortController | null>(null)
  useEffect(() => () => controller.current?.abort(), [runId])

  const close = () => {
    controller.current?.abort()
    controller.current = null
    setBusy(null)
    setOpen(false)
  }
  const doExport = async (format: EvalExportFormat) => {
    if (controller.current) return
    const current = new AbortController()
    controller.current = current
    setBusy(format)
    setProgress({ loaded: 0, total: 0 })
    try {
      const data = await fetchEvalExport(runId, current.signal, (loaded, total) => setProgress({ loaded, total }))
      if (current.signal.aborted) return
      downloadEvalExport(data, format)
      setOpen(false)
      toast.success(`已导出 ${data.items.length} 条评测结果`)
    } catch (error) {
      if (!current.signal.aborted) {
        current.abort()
        toast.error('导出失败', { description: formatApiError(error).message })
      }
    } finally {
      if (controller.current === current) {
        controller.current = null
        setBusy(null)
      }
    }
  }

  return (
    <>
      <Button size="sm" variant="outline" onClick={() => setOpen(true)} data-testid="export-eval">
        <Download className="h-3.5 w-3.5" />
        导出评测
      </Button>
      <Dialog
        open={open}
        onOpenChange={(value) => { if (!value) close() }}
        title="导出评测"
        description="导出全部用例及每次重复执行的结果，不受当前筛选和分页影响。"
        size="sm"
        footer={<Button variant="ghost" onClick={close}>{busy ? '取消导出' : '取消'}</Button>}
      >
        <div className="space-y-3 text-xs">
          <p className="text-muted-foreground">包含完整输入输出、多轮对话、各评估器分数与理由，以及耗时、tokens 和成本。</p>
          {isRunActive(status) && (
            <p className="text-warning">评测仍在进行，结果会随执行进度变化；最终结果请在结束后导出。</p>
          )}
          <div className="flex gap-2">
            <Button variant="outline" disabled={!!busy} loading={busy === 'csv'} onClick={() => void doExport('csv')} data-testid="export-eval-csv">
              CSV 明细
            </Button>
            <Button variant="outline" disabled={!!busy} loading={busy === 'json'} onClick={() => void doExport('json')} data-testid="export-eval-json">
              JSON 完整结果
            </Button>
          </div>
          {busy && <p role="status" className="text-muted-foreground">正在读取完整结果：{progress.loaded} / {progress.total}</p>}
        </div>
      </Dialog>
    </>
  )
}
