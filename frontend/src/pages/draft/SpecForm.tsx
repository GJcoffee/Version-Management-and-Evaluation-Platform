import { useEffect, useMemo, useState } from 'react'

import { useProviders } from '@/api/queries/providers'
import type { SpecSchema } from '@/api/types'
import { AssetRefListEditor, AssetRefPicker } from '@/components/AssetRefPicker'
import { Field, Textarea } from '@/components/ui/Input'
import { KeyValueEditor } from '@/components/ui/KeyValueEditor'
import { parseAssetRef } from '@/lib/assetRef'
import { cn } from '@/lib/cn'
import { schemaAt } from '@/lib/specSchema'
import { getIn, type PathSeg } from '@/lib/specYaml'
import { CheckboxField, NumberField, Section, SelectField, TextField } from './formFields'

/** 一次表单修改：路径 + 新值（undefined 表示删除该键） */
export type SpecChange = [PathSeg[], unknown]

export interface SpecFormProps {
  spec: Record<string, unknown>
  kind: 'config' | 'code'
  schema?: SpecSchema
  onEdit: (changes: SpecChange[]) => void
  disabled?: boolean
}

export function SpecForm({ spec, kind, schema, onEdit, disabled }: SpecFormProps) {
  const ctx: FormCtx = {
    spec,
    schema,
    kind,
    disabled,
    set: (path, value) => onEdit([[path, value]]),
    edit: onEdit,
  }
  return (
    <div className="space-y-3" data-testid="spec-form">
      {kind === 'config' ? <ConfigForm ctx={ctx} /> : <CodeForm ctx={ctx} />}
    </div>
  )
}

interface FormCtx {
  spec: Record<string, unknown>
  schema?: SpecSchema
  kind: 'config' | 'code'
  disabled?: boolean
  set: (path: PathSeg[], value: unknown) => void
  edit: (changes: SpecChange[]) => void
}

const sch = (ctx: FormCtx, path: string[]) => schemaAt(ctx.schema, ctx.kind, path)

/* ───────────────────────────── 配置型 ───────────────────────────── */

function ConfigForm({ ctx }: { ctx: FormCtx }) {
  const { spec, disabled } = ctx
  const numField = (label: string, path: string[], integer?: boolean) => (
    <NumberField
      label={label}
      value={getIn(spec, path)}
      schema={sch(ctx, path)}
      integer={integer}
      disabled={disabled}
      onChange={(v) => ctx.set(path, v)}
    />
  )
  return (
    <>
      <Section title="模型" description="model">
        <ModelFields ctx={ctx} />
        <div className="grid grid-cols-2 gap-3">
          {numField('temperature', ['model', 'params', 'temperature'])}
          {numField('top_p', ['model', 'params', 'top_p'])}
          {numField('max_tokens', ['model', 'params', 'max_tokens'], true)}
          {numField('seed', ['model', 'params', 'seed'], true)}
        </div>
      </Section>

      <Section title="提示词" description="prompts">
        <SystemPromptField ctx={ctx} />
        <Field label="变量" hint="Jinja2 变量，模板中用 {{ variables.xxx }} 引用">
          <VariablesEditor
            value={(getIn(spec, ['prompts', 'variables']) as Record<string, unknown> | undefined) ?? {}}
            disabled={disabled}
            onChange={(v) => ctx.set(['prompts', 'variables'], v)}
          />
        </Field>
      </Section>

      <Section title="工具" description="tools">
        <ToolsEditor ctx={ctx} />
      </Section>

      <Section title="知识注入" description="knowledge.inject" defaultOpen={false}>
        <Field label="全文注入的知识" hint="模板中用 {{ knowledge['资产名'] }} 引用">
          <AssetRefListEditor
            kind="knowledge"
            disabled={disabled}
            value={asStringRefs(getIn(spec, ['knowledge', 'inject']))}
            onChange={(refs) => ctx.set(['knowledge', 'inject'], refs)}
          />
        </Field>
      </Section>

      <Section title="运行时" description="runtime" defaultOpen={false}>
        <div className="grid grid-cols-2 gap-3">
          {numField('max_steps', ['runtime', 'max_steps'], true)}
          {numField('turn_timeout_s', ['runtime', 'turn_timeout_s'])}
          {numField('tool_timeout_s', ['runtime', 'tool_timeout_s'])}
        </div>
        <CheckboxField
          label="parallel_tool_calls（并行工具调用）"
          checked={getIn(spec, ['runtime', 'parallel_tool_calls']) !== false}
          disabled={disabled}
          onChange={(v) => ctx.set(['runtime', 'parallel_tool_calls'], v)}
        />
      </Section>
    </>
  )
}

function asStringRefs(v: unknown): string[] {
  return Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : []
}

function ModelFields({ ctx }: { ctx: FormCtx }) {
  const providers = useProviders()
  const provider = String(getIn(ctx.spec, ['model', 'provider']) ?? '')
  const name = String(getIn(ctx.spec, ['model', 'name']) ?? '')
  const list = providers.data ?? []
  const current = list.find((p) => p.name === provider)
  const models = current?.models ?? []
  return (
    <div className="grid grid-cols-2 gap-3">
      <SelectField
        label="Provider"
        required
        value={provider}
        disabled={ctx.disabled}
        placeholder={list.length === 0 ? '尚未登记 Provider' : '请选择 Provider'}
        options={list.map((p) => ({ value: p.name }))}
        hint={list.length === 0 ? '请先到“设置”页登记 Provider' : undefined}
        onChange={(v) => {
          const next = list.find((p) => p.name === v)
          const changes: SpecChange[] = [[['model', 'provider'], v]]
          // 切换 Provider 后，原模型不在新 Provider 的模型列表里时，换成它的第一个模型
          if (next && next.models.length > 0 && !next.models.some((m) => m.name === name)) {
            changes.push([['model', 'name'], next.models[0].name])
          }
          ctx.edit(changes)
        }}
      />
      {models.length > 0 ? (
        <SelectField
          label="模型"
          required
          value={name}
          disabled={ctx.disabled}
          options={models.map((m) => ({ value: m.name }))}
          onChange={(v) => ctx.set(['model', 'name'], v)}
        />
      ) : (
        <TextField
          label="模型"
          required
          mono
          value={name}
          disabled={ctx.disabled}
          hint={current ? '该 Provider 未登记模型列表，请手动填写' : undefined}
          onChange={(v) => ctx.set(['model', 'name'], v ?? '')}
        />
      )}
    </div>
  )
}

function SystemPromptField({ ctx }: { ctx: FormCtx }) {
  const value = getIn(ctx.spec, ['prompts', 'system'])
  const parsed = parseAssetRef(value)
  const [forceRef, setForceRef] = useState(false)
  const mode: 'ref' | 'inline' = parsed || forceRef ? 'ref' : 'inline'
  const text = typeof value === 'string' && !parsed ? value : ''
  const tab = (m: 'ref' | 'inline', label: string) => (
    <button
      type="button"
      disabled={ctx.disabled}
      onClick={() => {
        if (m === 'inline') {
          setForceRef(false)
          if (parsed) ctx.set(['prompts', 'system'], '')
        } else setForceRef(true)
      }}
      className={cn(
        'rounded px-2 py-0.5 text-xs',
        mode === m ? 'bg-primary/10 font-medium text-primary' : 'text-muted-foreground hover:text-foreground',
      )}
    >
      {label}
    </button>
  )
  return (
    <Field label="system 提示词" hint="可引用提示词资产（支持 @latest 或固定修订），或直接写内联文本">
      <div className="mb-1 flex gap-1">
        {tab('ref', '资产引用')}
        {tab('inline', '内联文本')}
      </div>
      {mode === 'ref' ? (
        <AssetRefPicker
          kind="prompt"
          disabled={ctx.disabled}
          value={parsed && typeof value === 'string' ? value : null}
          onChange={(ref) => ctx.set(['prompts', 'system'], ref ?? '')}
        />
      ) : (
        <Textarea
          mono
          rows={6}
          value={text}
          disabled={ctx.disabled}
          placeholder="你是一个乐于助人的助手。"
          onChange={(e) => ctx.set(['prompts', 'system'], e.target.value)}
        />
      )}
    </Field>
  )
}

/** 变量：值按文本编辑；原值不是字符串时按 JSON 解析回写（数字、布尔保持原类型） */
function VariablesEditor({
  value,
  onChange,
  disabled,
}: {
  value: Record<string, unknown>
  onChange: (v: Record<string, unknown>) => void
  disabled?: boolean
}) {
  const asText = useMemo(
    () => Object.fromEntries(Object.entries(value).map(([k, v]) => [k, typeof v === 'string' ? v : JSON.stringify(v)])),
    [value],
  )
  return (
    <KeyValueEditor
      value={asText}
      disabled={disabled}
      keyPlaceholder="变量名"
      valuePlaceholder="值"
      addLabel="添加变量"
      onChange={(next) => {
        const out: Record<string, unknown> = {}
        for (const [k, t] of Object.entries(next)) {
          if (k in value && typeof value[k] !== 'string') {
            try {
              out[k] = JSON.parse(t)
              continue
            } catch {
              /* 保持为字符串 */
            }
          }
          out[k] = t
        }
        onChange(out)
      }}
    />
  )
}

interface BuiltinEntry {
  builtin: string
  with?: { sources?: unknown[]; top_k?: number }
}
const isBuiltinKnowledge = (e: unknown): e is BuiltinEntry =>
  !!e && typeof e === 'object' && (e as BuiltinEntry).builtin === 'knowledge_search'

function ToolsEditor({ ctx }: { ctx: FormCtx }) {
  const raw = getIn(ctx.spec, ['tools'])
  const tools: unknown[] = Array.isArray(raw) ? raw : []
  const setTools = (next: unknown[]) => ctx.set(['tools'], next)
  const builtinIdx = tools.findIndex(isBuiltinKnowledge)
  const builtin = builtinIdx >= 0 ? (tools[builtinIdx] as BuiltinEntry) : null
  const patchBuiltin = (patch: Partial<NonNullable<BuiltinEntry['with']>>) =>
    setTools(tools.map((t, i) => (i === builtinIdx ? { ...builtin, with: { ...builtin?.with, ...patch } } : t)))

  return (
    <div className="space-y-4">
      <Field label="工具资产" hint="引用 tool 资产；运行时以工具定义里的 name 暴露给模型">
        <div className="space-y-1.5">
          {tools.map((t, i) => {
            if (isBuiltinKnowledge(t)) return null
            const ref = typeof t === 'string' ? parseAssetRef(t) : null
            if (ref && ref.kind === 'tool' && typeof t === 'string') {
              return (
                <AssetRefPicker
                  key={`${i}-${ref.name}`}
                  kind="tool"
                  value={t}
                  clearable
                  disabled={ctx.disabled}
                  onChange={(next) => setTools(next === null ? tools.filter((_, j) => j !== i) : tools.map((x, j) => (j === i ? next : x)))}
                />
              )
            }
            return (
              <div key={i} className="flex items-center gap-2 rounded-md border border-dashed border-border px-2 py-1 text-xs">
                <code className="min-w-0 flex-1 truncate" title={JSON.stringify(t)}>
                  {JSON.stringify(t)}
                </code>
                <span className="shrink-0 text-muted-foreground">请在 YAML 中编辑</span>
              </div>
            )
          })}
          <AssetRefPicker
            kind="tool"
            value={null}
            disabled={ctx.disabled}
            placeholder="+ 添加工具"
            onChange={(next) => next && setTools([...tools, next])}
          />
        </div>
      </Field>

      <div className="rounded-md border border-border p-3">
        <CheckboxField
          label="内置工具 knowledge_search（检索知识库）"
          checked={!!builtin}
          disabled={ctx.disabled}
          onChange={(on) =>
            setTools(
              on
                ? [...tools, { builtin: 'knowledge_search', with: { sources: [], top_k: 5 } }]
                : tools.filter((_, i) => i !== builtinIdx),
            )
          }
        />
        {builtin && (
          <div className="mt-3 space-y-3 pl-6">
            <Field label="检索来源（knowledge 资产）">
              <AssetRefListEditor
                kind="knowledge"
                disabled={ctx.disabled}
                value={asStringRefs(builtin.with?.sources)}
                onChange={(refs) => patchBuiltin({ sources: refs })}
              />
            </Field>
            <div className="max-w-40">
              <NumberField
                label="top_k"
                value={builtin.with?.top_k}
                integer
                disabled={ctx.disabled}
                schema={{ type: 'integer', minimum: 1, maximum: 100, default: 5 }}
                onChange={(v) => patchBuiltin({ top_k: v })}
              />
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

/* ───────────────────────────── 代码型 ───────────────────────────── */

function CodeForm({ ctx }: { ctx: FormCtx }) {
  const { spec, disabled } = ctx
  const transport = String(getIn(spec, ['transport']) ?? 'http')
  const strMap = (path: string[]): Record<string, string> => {
    const v = getIn(spec, path)
    return v && typeof v === 'object' ? (v as Record<string, string>) : {}
  }
  const command = getIn(spec, ['subprocess', 'command'])
  return (
    <>
      <Section title="传输方式" description="transport">
        <SelectField
          label="transport"
          value={transport}
          disabled={disabled}
          options={[
            { value: 'http', label: 'http（evalp-agent/1 协议）' },
            { value: 'subprocess', label: 'subprocess（子进程，stdin/stdout）' },
          ]}
          onChange={(v) => ctx.set(['transport'], v)}
        />
        {transport === 'http' ? (
          <div className="space-y-3">
            <TextField
              label="base_url"
              required
              mono
              value={getIn(spec, ['http', 'base_url'])}
              disabled={disabled}
              placeholder="http://127.0.0.1:8001"
              onChange={(v) => ctx.set(['http', 'base_url'], v ?? '')}
            />
            <NumberField
              label="timeout_s"
              value={getIn(spec, ['http', 'timeout_s'])}
              schema={sch(ctx, ['http', 'timeout_s'])}
              disabled={disabled}
              onChange={(v) => ctx.set(['http', 'timeout_s'], v)}
            />
            <Field label="请求头" hint="可用 ${env:NAME} 引用环境变量，不会保存实际值">
              <KeyValueEditor
                value={strMap(['http', 'headers'])}
                disabled={disabled}
                keyPlaceholder="Header"
                addLabel="添加请求头"
                onChange={(v) => ctx.set(['http', 'headers'], Object.keys(v).length ? v : undefined)}
              />
            </Field>
          </div>
        ) : (
          <div className="space-y-3">
            <Field label="command" required htmlFor="sp-command" hint="每行一个参数，例如 python / -m / my_agent.entry">
              <Textarea
                id="sp-command"
                mono
                rows={3}
                value={Array.isArray(command) ? command.join('\n') : ''}
                disabled={disabled}
                onChange={(e) =>
                  ctx.set(
                    ['subprocess', 'command'],
                    e.target.value === '' ? [] : e.target.value.split('\n'),
                  )
                }
              />
            </Field>
            <TextField
              label="cwd"
              mono
              value={getIn(spec, ['subprocess', 'cwd'])}
              disabled={disabled}
              placeholder="."
              onChange={(v) => ctx.set(['subprocess', 'cwd'], v)}
            />
            <Field label="环境变量">
              <KeyValueEditor
                value={strMap(['subprocess', 'env'])}
                disabled={disabled}
                keyPlaceholder="NAME"
                addLabel="添加环境变量"
                onChange={(v) => ctx.set(['subprocess', 'env'], Object.keys(v).length ? v : undefined)}
              />
            </Field>
          </div>
        )}
      </Section>

      <Section title="代码来源" description="source">
        <TextField
          label="repo"
          mono
          value={getIn(spec, ['source', 'repo'])}
          disabled={disabled}
          placeholder="."
          hint="agent 代码所在的 git 仓库，建议填绝对路径"
          onChange={(v) => ctx.set(['source', 'repo'], v)}
        />
        <CheckboxField
          label="capture_git（发布时采集 commit / branch / dirty）"
          checked={getIn(spec, ['source', 'capture_git']) !== false}
          disabled={disabled}
          onChange={(v) => ctx.set(['source', 'capture_git'], v)}
        />
        <CheckboxField
          label="allow_dirty（允许未提交改动，补丁参与版本哈希）"
          checked={getIn(spec, ['source', 'allow_dirty']) === true}
          disabled={disabled}
          onChange={(v) => ctx.set(['source', 'allow_dirty'], v)}
        />
      </Section>

      <Section title="参数" description="params（原样透传给 agent）">
        <JsonObjectField
          value={(getIn(spec, ['params']) as Record<string, unknown> | undefined) ?? {}}
          disabled={disabled}
          onChange={(v) => ctx.set(['params'], v)}
        />
      </Section>

      <Section title="运行时" description="runtime" defaultOpen={false}>
        <NumberField
          label="turn_timeout_s"
          value={getIn(spec, ['runtime', 'turn_timeout_s'])}
          schema={sch(ctx, ['runtime', 'turn_timeout_s'])}
          disabled={disabled}
          onChange={(v) => ctx.set(['runtime', 'turn_timeout_s'], v)}
        />
      </Section>
    </>
  )
}

/** JSON 对象输入：本地保留文本，解析成功才回写 */
export function JsonObjectField({
  value,
  onChange,
  disabled,
}: {
  value: Record<string, unknown>
  onChange: (v: Record<string, unknown>) => void
  disabled?: boolean
}) {
  const canonical = JSON.stringify(value, null, 2)
  const [text, setText] = useState(canonical)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    setText((cur) => {
      try {
        if (JSON.stringify(JSON.parse(cur)) === JSON.stringify(value)) return cur
      } catch {
        /* 本地文本不合法时，外部变化仍然覆盖 */
      }
      return canonical
    })
  }, [value, canonical])
  return (
    <div className="space-y-1">
      <Textarea
        mono
        rows={5}
        value={text}
        disabled={disabled}
        invalid={!!error}
        onChange={(e) => {
          const t = e.target.value
          setText(t)
          try {
            const v = JSON.parse(t === '' ? '{}' : t) as unknown
            if (v === null || typeof v !== 'object' || Array.isArray(v)) throw new Error('必须是 JSON 对象')
            setError(null)
            onChange(v as Record<string, unknown>)
          } catch (err) {
            setError(err instanceof Error ? err.message : '不是合法的 JSON')
          }
        }}
      />
      {error && <p className="text-xs text-danger">JSON 无效：{error}</p>}
    </div>
  )
}

