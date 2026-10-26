import { useState } from 'react'
import { Link } from 'react-router-dom'
import { CheckCircle2, ShieldCheck, XCircle } from 'lucide-react'
import type { EvalGateResult, EvalRunDetailOut } from '@/api/evalTypes'
import { useEvalGate, useEvals } from '@/api/queries/evals'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/Card'
import { Field, Input } from '@/components/ui/Input'
import { Select } from '@/components/ui/Select'
import { formatApiError } from '@/lib/apiError'
import { formatPercent } from '@/lib/format'
import { compareHref, formatPValue, formatSigned, runDatasetLabel, runTitle, runVersionLabel } from './evalUtils'

/** 门禁结论：通过 / 不通过、原因与关键数字 */
export function GateResultView({ result }: { result: EvalGateResult }) {
  const s = result.summary
  return (
    <div className="space-y-2" data-testid="gate-result" data-passed={result.passed}>
      <div className="flex items-center gap-2">
        {result.passed ? <CheckCircle2 className="h-5 w-5 text-success" /> : <XCircle className="h-5 w-5 text-danger" />}
        <Badge variant={result.passed ? 'success' : 'danger'} data-testid="gate-verdict">
          {result.passed ? '门禁通过' : '门禁未通过'}
        </Badge>
      </div>
      {result.reasons.length > 0 && (
        <ul className="list-disc space-y-0.5 pl-5 text-xs text-danger" data-testid="gate-reasons">
          {result.reasons.map((r, i) => (
            <li key={i}>{r}</li>
          ))}
        </ul>
      )}
      <div className="flex flex-wrap gap-x-5 gap-y-1 font-mono text-xs text-muted-foreground">
        <span>通过率 {formatPercent(s.pass_rate, 1)}</span>
        {s.min_pass_rate !== null && <span>要求 ≥ {formatPercent(s.min_pass_rate, 1)}</span>}
        {s.baseline_run_id && <span>基线通过率 {formatPercent(s.baseline_pass_rate ?? null, 1)}</span>}
        {s.regressions !== undefined && (
          <span>
            退化 {s.regressions}（允许 {s.max_regressions}）· 改善 {s.improved ?? 0} · 不变 {s.unchanged ?? 0}
          </span>
        )}
        {s.mean_delta !== undefined && s.mean_delta !== null && <span>平均 Δ {formatSigned(s.mean_delta, 3)}</span>}
        {s.sign_test_p !== undefined && s.sign_test_p !== null && <span>符号检验 p = {formatPValue(s.sign_test_p)}</span>}
      </div>
      {s.regressed_cases && s.regressed_cases.length > 0 && (
        <div className="text-xs" data-testid="gate-regressed">
          <div className="mb-1 text-muted-foreground">退化的用例{s.regressed_cases.length >= 50 ? '（最多显示 50 个）' : ''}</div>
          <div className="flex flex-wrap gap-1.5">
            {s.regressed_cases.map((c) => (
              <Badge key={c.case_key} variant="outline" className="font-mono">
                {c.case_key} {formatSigned(c.delta, 2)}
              </Badge>
            ))}
          </div>
          {s.baseline_run_id && (
            <Link to={compareHref(s.baseline_run_id, s.run_id)} className="mt-1.5 inline-block text-primary hover:underline">
              在评测对比页查看详情
            </Link>
          )}
        </div>
      )}
    </div>
  )
}

/**
 * CI 门禁：可选一个基线评测，要求退化用例数不超过上限、通过率不低于下限。
 * 对应接口 GET /evals/{id}/gate（CI 脚本也可直接调用）。
 */
export function GatePanel({ run }: { run: Pick<EvalRunDetailOut, 'id' | 'agent_id' | 'status'> }) {
  const gate = useEvalGate()
  const runs = useEvals({ agent_id: run.agent_id, status: 'completed,completed_with_errors', limit: 50 })
  const candidates = (runs.data?.pages.flatMap((p) => p.items) ?? []).filter((r) => r.id !== run.id)
  const [baseline, setBaseline] = useState('')
  const [maxReg, setMaxReg] = useState('0')
  const [minPass, setMinPass] = useState('')
  const [threshold, setThreshold] = useState('0.05')
  const [showErrors, setShowErrors] = useState(false)

  const maxRegN = /^\d+$/.test(maxReg.trim()) ? Number(maxReg) : null
  const minPassN = minPass.trim() === '' ? undefined : Number(minPass)
  const minPassBad = minPassN !== undefined && (!Number.isFinite(minPassN) || minPassN < 0 || minPassN > 100)
  const thresholdN = Number(threshold)
  const thresholdBad = threshold.trim() === '' || !Number.isFinite(thresholdN) || thresholdN < 0
  const finished = run.status === 'completed' || run.status === 'completed_with_errors'

  const submit = () => {
    setShowErrors(true)
    if (maxRegN === null || minPassBad || thresholdBad) return
    gate.mutate({
      id: run.id,
      baseline: baseline || undefined,
      max_regressions: maxRegN,
      min_pass_rate: minPassN === undefined ? undefined : minPassN / 100,
      threshold: thresholdN,
    })
  }
  const err = gate.error ? formatApiError(gate.error, '门禁检查失败') : null

  return (
    <Card data-testid="gate-panel">
      <CardHeader>
        <div>
          <CardTitle className="flex items-center gap-1.5">
            <ShieldCheck className="h-4 w-4" />
            CI 门禁
          </CardTitle>
          <CardDescription>
            检查这次评测能否放行：相对基线退化的用例数不超过上限，且通过率不低于下限。CI 里可调用 GET /api/v1/evals/{run.id}/gate。
          </CardDescription>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="grid gap-3 md:grid-cols-4">
          <Field label="基线评测" hint="可选；不选则只检查通过率">
            <Select aria-label="基线评测" value={baseline} onChange={(e) => setBaseline(e.target.value)} data-testid="gate-baseline">
              <option value="">不比较基线</option>
              {candidates.map((r) => (
                <option key={r.id} value={r.id}>
                  {runTitle(r)} · {runVersionLabel(r)} · {runDatasetLabel(r)}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="允许退化的用例数" hint="超过则不通过" error={showErrors && maxRegN === null ? '请输入不小于 0 的整数' : null}>
            <Input type="number" min={0} aria-label="允许退化的用例数" value={maxReg} onChange={(e) => setMaxReg(e.target.value)} disabled={!baseline} />
          </Field>
          <Field label="最低通过率（%）" hint="可选，0–100" error={showErrors && minPassBad ? '请输入 0 到 100 之间的数' : null}>
            <Input type="number" min={0} max={100} step="any" aria-label="最低通过率" value={minPass} onChange={(e) => setMinPass(e.target.value)} placeholder="不限制" />
          </Field>
          <Field label="退化阈值 Δ" hint="分数变化超过该值才算退化" error={showErrors && thresholdBad ? '请输入不小于 0 的数' : null}>
            <Input type="number" min={0} step="any" aria-label="退化阈值" value={threshold} onChange={(e) => setThreshold(e.target.value)} disabled={!baseline} />
          </Field>
        </div>
        <div className="flex items-center gap-3">
          <Button size="sm" onClick={submit} loading={gate.isPending} data-testid="run-gate">
            检查门禁
          </Button>
          {!finished && <span className="text-xs text-muted-foreground">评测还没有成功结束，现在检查会得到「未通过」。</span>}
        </div>
        {err && (
          <p role="alert" className="text-xs text-danger">
            {err.message}
          </p>
        )}
        {gate.data && <GateResultView result={gate.data} />}
      </CardContent>
    </Card>
  )
}
