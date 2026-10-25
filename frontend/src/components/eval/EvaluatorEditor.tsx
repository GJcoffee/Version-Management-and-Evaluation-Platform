import { Plus, Trash2 } from 'lucide-react'
import { useProviders } from '@/api/queries/providers'
import { AssetRefPicker } from '@/components/AssetRefPicker'
import { Button } from '@/components/ui/Button'
import { Field, Input, Textarea } from '@/components/ui/Input'
import { Select } from '@/components/ui/Select'
import { Badge } from '@/components/ui/Badge'
import {
  EVALUATOR_TYPE_LABEL,
  newEvaluatorForm,
  validateEvaluators,
  type EvaluatorForm,
  type EvaluatorFormType,
} from './evalUtils'

const NORMALIZE_OPTIONS: Array<{ value: string; label: string }> = [
  { value: 'strip', label: '去首尾空白' },
  { value: 'lower', label: '转小写' },
  { value: 'nfkc', label: '全半角归一' },
  { value: 'punct', label: '去标点' },
]
const REGEX_FLAGS: Array<{ value: string; label: string }> = [
  { value: 'i', label: 'i 忽略大小写' },
  { value: 'm', label: 'm 多行' },
  { value: 's', label: 's 点匹配换行' },
]

function Check({ checked, onChange, children }: { checked: boolean; onChange: (v: boolean) => void; children: React.ReactNode }) {
  return (
    <label className="inline-flex cursor-pointer items-center gap-1.5 text-xs">
      <input type="checkbox" className="h-3.5 w-3.5 accent-primary" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      {children}
    </label>
  )
}

const toggleIn = (list: string[], v: string, on: boolean) => (on ? [...list.filter((x) => x !== v), v] : list.filter((x) => x !== v))

function JudgeFields({ value, onChange }: { value: EvaluatorForm; onChange: (patch: Partial<EvaluatorForm>) => void }) {
  const providers = useProviders()
  const list = providers.data ?? []
  const current = list.find((p) => p.name === value.provider)
  const models = current?.models ?? []
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-3">
        <Field label="Provider" required>
          <Select
            aria-label="评审 Provider"
            value={value.provider}
            onChange={(e) => {
              const p = list.find((x) => x.name === e.target.value)
              onChange({ provider: e.target.value, model: p?.models[0]?.name ?? '' })
            }}
          >
            <option value="">选择 Provider…</option>
            {list.map((p) => (
              <option key={p.id} value={p.name}>
                {p.name}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="模型" required>
          <Select aria-label="评审模型" value={value.model} disabled={!current} onChange={(e) => onChange({ model: e.target.value })}>
            <option value="">选择模型…</option>
            {models.map((m) => (
              <option key={m.name} value={m.name}>
                {m.name}
              </option>
            ))}
          </Select>
        </Field>
      </div>
      {providers.isSuccess && list.length === 0 && <p className="text-xs text-warning">还没有配置 Provider，请先到「设置」中添加。</p>}
      <Field label="评审提示词" required hint="变量：input、output、expected、rubric；要求模型只输出 {&quot;score&quot;: 整数, &quot;reason&quot;: &quot;…&quot;}">
        <AssetRefPicker kind="prompt" value={value.prompt} onChange={(ref) => onChange({ prompt: ref })} />
      </Field>
      <div className="grid grid-cols-3 gap-3">
        <Field label="量程最小值">
          <Input type="number" aria-label="量程最小值" value={value.scaleMin} onChange={(e) => onChange({ scaleMin: e.target.value })} />
        </Field>
        <Field label="量程最大值">
          <Input type="number" aria-label="量程最大值" value={value.scaleMax} onChange={(e) => onChange({ scaleMax: e.target.value })} />
        </Field>
        <Field label="通过阈值" hint="留空取量程 60% 处">
          <Input type="number" aria-label="通过阈值" value={value.passThreshold} onChange={(e) => onChange({ passThreshold: e.target.value })} />
        </Field>
      </div>
    </div>
  )
}

function EvaluatorCard({
  value,
  error,
  onChange,
  onRemove,
}: {
  value: EvaluatorForm
  error?: string
  onChange: (patch: Partial<EvaluatorForm>) => void
  onRemove: () => void
}) {
  return (
    <div className="space-y-3 rounded-md border border-border p-3" data-testid={`evaluator-${value.key}`}>
      <div className="flex items-center gap-2">
        <Badge variant="info">{EVALUATOR_TYPE_LABEL[value.type]}</Badge>
        <Input
          mono
          aria-label="评估器 key"
          className="w-40"
          value={value.key}
          invalid={!!error && /key/.test(error)}
          onChange={(e) => onChange({ key: e.target.value })}
        />
        <Check checked={value.gate} onChange={(v) => onChange({ gate: v })}>
          参与通过判定
        </Check>
        <Button size="icon" variant="ghost" className="ml-auto h-7 w-7" aria-label={`删除评估器 ${value.key}`} onClick={onRemove}>
          <Trash2 className="h-3.5 w-3.5" />
        </Button>
      </div>

      {(value.type === 'exact_match' || value.type === 'contains') && (
        <Field label="期望值" hint="每行一个；留空则使用用例的 expected">
          <Textarea
            rows={2}
            mono
            aria-label="期望值"
            value={value.values}
            onChange={(e) => onChange({ values: e.target.value })}
            placeholder={value.type === 'contains' ? '退货\n七天无理由' : '（留空 = 与用例 expected 比较）'}
          />
        </Field>
      )}
      {value.type === 'exact_match' && (
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
          <span className="text-xs text-muted-foreground">比较前处理</span>
          {NORMALIZE_OPTIONS.map((o) => (
            <Check key={o.value} checked={value.normalize.includes(o.value)} onChange={(v) => onChange({ normalize: toggleIn(value.normalize, o.value, v) })}>
              {o.label}
            </Check>
          ))}
        </div>
      )}
      {value.type === 'contains' && (
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
          <Select aria-label="匹配方式" className="w-40" value={value.mode} onChange={(e) => onChange({ mode: e.target.value as 'all' | 'any' })}>
            <option value="all">全部都要包含</option>
            <option value="any">包含任意一个</option>
          </Select>
          <Check checked={value.negate} onChange={(v) => onChange({ negate: v })}>
            取反（不能包含）
          </Check>
          <Check checked={value.caseSensitive} onChange={(v) => onChange({ caseSensitive: v })}>
            区分大小写
          </Check>
        </div>
      )}
      {value.type === 'regex' && (
        <>
          <Field label="正则表达式" required>
            <Input mono aria-label="正则表达式" value={value.pattern} onChange={(e) => onChange({ pattern: e.target.value })} placeholder="订单\s*A\d+" />
          </Field>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
            <span className="text-xs text-muted-foreground">标志</span>
            {REGEX_FLAGS.map((o) => (
              <Check key={o.value} checked={value.flags.includes(o.value)} onChange={(v) => onChange({ flags: toggleIn(value.flags, o.value, v) })}>
                {o.label}
              </Check>
            ))}
          </div>
        </>
      )}
      {value.type === 'llm_judge' && <JudgeFields value={value} onChange={onChange} />}
      {error && <p className="text-xs text-danger">{error}</p>}
    </div>
  )
}

/** 评估器编辑器：可添加四种评估器，每种有对应表单。`showErrors` 为 true 时显示校验错误 */
export function EvaluatorEditor({
  value,
  onChange,
  showErrors,
}: {
  value: EvaluatorForm[]
  onChange: (next: EvaluatorForm[]) => void
  showErrors?: boolean
}) {
  const v = validateEvaluators(value)
  const add = (type: EvaluatorFormType) => onChange([...value, newEvaluatorForm(type, value.map((e) => e.key))])
  return (
    <div className="space-y-2" data-testid="evaluator-editor">
      {value.map((e) => (
        <EvaluatorCard
          key={e.uid}
          value={e}
          error={showErrors ? v.byUid[e.uid] : undefined}
          onChange={(patch) => onChange(value.map((x) => (x.uid === e.uid ? { ...x, ...patch } : x)))}
          onRemove={() => onChange(value.filter((x) => x.uid !== e.uid))}
        />
      ))}
      {showErrors && v.global && <p className="text-xs text-danger">{v.global}</p>}
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs text-muted-foreground">添加评估器</span>
        {(Object.keys(EVALUATOR_TYPE_LABEL) as EvaluatorFormType[]).map((t) => (
          <Button key={t} size="sm" variant="outline" onClick={() => add(t)} data-testid={`add-evaluator-${t}`}>
            <Plus className="h-3.5 w-3.5" />
            {EVALUATOR_TYPE_LABEL[t]}
          </Button>
        ))}
      </div>
    </div>
  )
}
