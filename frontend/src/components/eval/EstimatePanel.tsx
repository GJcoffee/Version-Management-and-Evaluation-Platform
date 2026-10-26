import type { EvalEstimate } from '@/api/evalTypes'
import { formatCost, formatTokens } from '@/lib/format'

const BASIS_LABEL: Record<string, string> = {
  history: '依据该 Agent 在此数据集上的历史评测均值',
  heuristic: '没有历史评测，按输入长度 × 3 + max_tokens × 0.5 粗略估算',
  mixed: '部分版本有历史评测，其余为粗略估算',
}

/** 成本预估结果（POST /evals?dry_run=true）：条数、预计新增 tokens / 成本、是否超出预算 */
export function EstimatePanel({ estimate, stale }: { estimate: EvalEstimate; stale?: boolean }) {
  return (
    <div className="space-y-2 rounded-md border border-border bg-muted/30 px-3 py-2.5 text-xs" data-testid="estimate-panel" data-stale={stale ? 'true' : undefined}>
      <div className="flex flex-wrap items-baseline gap-x-5 gap-y-1">
        <span>
          共 <span className="font-mono text-sm font-semibold" data-testid="estimate-items">{estimate.items}</span> 次执行
          {estimate.cached_items > 0 && <span className="text-muted-foreground">（其中 {estimate.cached_items} 次命中缓存，不重复计费）</span>}
        </span>
        <span>
          预计新增 tokens <span className="font-mono text-sm font-semibold" data-testid="estimate-tokens">{formatTokens(estimate.tokens)}</span>
        </span>
        <span>
          预计新增成本{' '}
          <span className="font-mono text-sm font-semibold" data-testid="estimate-cost">
            {estimate.cost_known ? formatCost(estimate.cost) : '未知'}
          </span>
        </span>
      </div>
      <p className="text-muted-foreground">
        {BASIS_LABEL[estimate.basis] ?? estimate.basis}。仅为估算，实际消耗以评测结果为准；评审模型的调用不包含在内。
      </p>
      {!estimate.cost_known && <p className="text-warning">所选模型没有配置价格，成本无法估算（按 0 计）；可以在「设置」里补充价格。</p>}
      {estimate.exceeds_budget === true && (
        <p className="font-medium text-danger" data-testid="estimate-exceeds">
          预估超出所设预算：评测会在超出后自动暂停，可调高预算后继续。
        </p>
      )}
      {estimate.exceeds_budget === false && <p className="text-success">预估在预算之内。</p>}
      {estimate.per_version.length > 1 && (
        <ul className="font-mono text-muted-foreground">
          {estimate.per_version.map((v) => (
            <li key={v.version_id}>
              {v.version_id.slice(-6)}：{v.items} 次 · {formatTokens(v.tokens)} tokens · {estimate.cost_known ? formatCost(v.cost) : '-'}
            </li>
          ))}
        </ul>
      )}
      {stale && <p className="text-warning">配置已修改，这份预估已过期，请重新预估。</p>}
    </div>
  )
}
