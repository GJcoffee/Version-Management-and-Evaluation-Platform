import { useState } from 'react'
import { FlaskConical } from 'lucide-react'
import { EvalRunList } from '@/components/eval/EvalRunList'
import { NewEvalDialog } from '@/components/eval/NewEvalDialog'
import { Button } from '@/components/ui/Button'

/** 评测列表页：全部 Agent 的评测，可按状态筛选，勾选两个对比 */
export default function EvalsPage() {
  const [open, setOpen] = useState(false)
  const newButton = (
    <Button size="sm" onClick={() => setOpen(true)} data-testid="new-eval">
      <FlaskConical className="h-3.5 w-3.5" />
      发起评测
    </Button>
  )
  return (
    <div className="mx-auto max-w-6xl space-y-4 p-6">
      <div>
        <h1 className="text-lg font-semibold">评测</h1>
        <p className="text-xs text-muted-foreground">用数据集批量运行 Agent 版本，按评估器打分；勾选两个评测可逐用例对比。</p>
      </div>
      <EvalRunList actions={newButton} emptyAction={newButton} />
      {open && <NewEvalDialog onClose={() => setOpen(false)} />}
    </div>
  )
}
