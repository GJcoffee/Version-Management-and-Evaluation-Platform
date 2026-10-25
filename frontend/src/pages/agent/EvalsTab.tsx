import { useState } from 'react'
import { FlaskConical } from 'lucide-react'
import { EvalRunList } from '@/components/eval/EvalRunList'
import { NewEvalDialog } from '@/components/eval/NewEvalDialog'
import { Button } from '@/components/ui/Button'

/** 评测标签页：该 Agent 的评测列表与发起评测 */
export function EvalsTab({ agentId }: { agentId: string }) {
  const [open, setOpen] = useState(false)
  const newButton = (
    <Button size="sm" onClick={() => setOpen(true)} data-testid="new-eval">
      <FlaskConical className="h-3.5 w-3.5" />
      发起评测
    </Button>
  )
  return (
    <>
      <EvalRunList agentId={agentId} actions={newButton} emptyAction={newButton} />
      {open && <NewEvalDialog agentId={agentId} onClose={() => setOpen(false)} />}
    </>
  )
}
