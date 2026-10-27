import { useState } from 'react'
import { ApiError } from '@/api/client'
import { useUpdateAgent } from '@/api/queries/agents'
import type { Agent, AssetKind, Conventions } from '@/api/types'
import { Button } from '@/components/ui/Button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/Card'
import { Field, Input } from '@/components/ui/Input'
import { ASSET_KINDS, ASSET_KIND_LABEL } from '@/lib/assetRef'
import { toast } from '@/stores/toast'

/** 规范表单值 -> 提交给服务端的规范（参数名去掉首尾空白，空表示不开启） */
export function toConventions(v: Conventions): Conventions {
  return { ...v, tag_from_param: v.tag_from_param?.trim() || null }
}

export function sameConventions(a: Conventions, b: Conventions): boolean {
  const x = toConventions(a)
  const y = toConventions(b)
  return (
    x.tag_from_param === y.tag_from_param &&
    x.require_notes === y.require_notes &&
    x.snapshot_readonly === y.snapshot_readonly &&
    [...x.require_source_id].sort().join() === [...y.require_source_id].sort().join()
  )
}

/**
 * Agent 的发布 / 推送规范：查看与修改。规范存在服务端 agent 记录上，由服务端对 CLI、MCP、网页、HTTP API
 * 一致地强制（不放 agent.yaml）；违反时请求会被拒绝并给出具体原因。
 */
export function ConventionsTab({ agent }: { agent: Agent }) {
  const update = useUpdateAgent()
  const [value, setValue] = useState<Conventions>(agent.conventions)
  const [error, setError] = useState<string | null>(null)
  const dirty = !sameConventions(value, agent.conventions)

  const toggleKind = (kind: AssetKind, on: boolean) =>
    setValue((v) => ({
      ...v,
      require_source_id: on ? [...v.require_source_id, kind] : v.require_source_id.filter((k) => k !== kind),
    }))

  const save = async () => {
    setError(null)
    try {
      await update.mutateAsync({ ref: agent.id, body: { conventions: toConventions(value) } })
      toast.success('规范已保存')
    } catch (e) {
      setError(e instanceof ApiError ? e.message : '保存失败')
    }
  }

  return (
    <Card className="max-w-3xl">
      <CardHeader>
        <div>
          <CardTitle>发布规范</CardTitle>
          <CardDescription>
            由服务端对所有入口（CLI、MCP 助手、网页、HTTP API）一致地强制：违反时发布 / 推送 / 写入会被拒绝，并返回违反了哪条以及怎么改。
          </CardDescription>
        </div>
      </CardHeader>
      <CardContent className="space-y-5">
        <Field
          label="版本标签取自参数（tag_from_param）"
          htmlFor="conv-tag-param"
          hint="填写 params 里的参数名，如 hiagent_version。开启后发布必须带标签，且标签必须等于 v{参数值}（如 v0.5.3）；同一标签已存在于该 agent 时拒绝。留空表示不限制。"
        >
          <Input
            id="conv-tag-param"
            mono
            className="max-w-xs"
            placeholder="hiagent_version"
            value={value.tag_from_param ?? ''}
            onChange={(e) => setValue((v) => ({ ...v, tag_from_param: e.target.value }))}
          />
        </Field>

        <label className="flex cursor-pointer items-start gap-2 text-sm">
          <input
            type="checkbox"
            className="mt-0.5 h-4 w-4 accent-primary"
            checked={value.require_notes}
            onChange={(e) => setValue((v) => ({ ...v, require_notes: e.target.checked }))}
          />
          <span>
            发布说明必填（require_notes）
            <span className="block text-xs text-muted-foreground">发布时必须写明这次改了什么，否则拒绝。</span>
          </span>
        </label>

        <fieldset className="space-y-1.5">
          <legend className="text-sm">必须配置来源 ID 的资产类型（require_source_id）</legend>
          <p className="text-xs text-muted-foreground">
            快照 map 中这些类型的规则必须配置 id（如 id: &quot;$.id&quot;），否则拒绝推送和发布。来源 ID 让同一个 skill 改名后仍是同一个资产。
          </p>
          <div className="flex flex-wrap gap-x-4 gap-y-1.5 pt-1">
            {ASSET_KINDS.map((kind) => (
              <label key={kind} className="flex cursor-pointer items-center gap-1.5 text-sm">
                <input
                  type="checkbox"
                  className="h-4 w-4 accent-primary"
                  checked={value.require_source_id.includes(kind)}
                  onChange={(e) => toggleKind(kind, e.target.checked)}
                />
                {ASSET_KIND_LABEL[kind]}
              </label>
            ))}
          </div>
        </fieldset>

        <label className="flex cursor-pointer items-start gap-2 text-sm">
          <input
            type="checkbox"
            className="mt-0.5 h-4 w-4 accent-primary"
            checked={value.snapshot_readonly}
            onChange={(e) => setValue((v) => ({ ...v, snapshot_readonly: e.target.checked }))}
          />
          <span>
            快照资产只读（snapshot_readonly）
            <span className="block text-xs text-muted-foreground">
              来自 agent 快照的资产只能通过快照推送更新；新建修订、upsert、改描述、删除（含 MCP 与 HTTP 接口）一律拒绝。
            </span>
          </span>
        </label>

        {error && (
          <div role="alert" className="whitespace-pre-line rounded-md border border-danger/40 bg-danger/10 p-2 text-xs text-danger">
            {error}
          </div>
        )}
        <div className="flex items-center gap-2">
          <Button onClick={save} loading={update.isPending} disabled={!dirty}>
            保存
          </Button>
          <Button variant="ghost" disabled={!dirty || update.isPending} onClick={() => setValue(agent.conventions)}>
            还原
          </Button>
        </div>
      </CardContent>
    </Card>
  )
}
