import type { JsonSchemaNode, SpecSchema } from '@/api/types'

/** 解析 `#/$defs/Xxx` 引用 */
export function deref(schema: SpecSchema, node: JsonSchemaNode | undefined): JsonSchemaNode | undefined {
  let cur = node
  for (let i = 0; cur && cur.$ref && i < 8; i++) {
    const name = cur.$ref.replace('#/$defs/', '')
    const target = schema.$defs?.[name]
    // 引用节点上的 description/default 优先于目标
    cur = target ? { ...target, ...omitRef(cur) } : undefined
  }
  return cur
}

function omitRef(n: JsonSchemaNode): JsonSchemaNode {
  const { $ref: _ref, ...rest } = n
  return rest
}

/** `anyOf: [X, null]` 取 X（可空类型） */
export function unwrapNullable(schema: SpecSchema, node: JsonSchemaNode | undefined): JsonSchemaNode | undefined {
  const n = deref(schema, node)
  if (n?.anyOf) {
    const nonNull = n.anyOf.filter((x) => x.type !== 'null')
    if (nonNull.length === 1) {
      const inner = deref(schema, nonNull[0])
      return { ...inner, title: n.title ?? inner?.title, description: n.description ?? inner?.description, default: n.default }
    }
  }
  return n
}

/** 取某类 Spec 下一个路径的 schema 节点，例如 ('config', ['model','params','temperature']) */
export function schemaAt(schema: SpecSchema | undefined, kind: 'config' | 'code', path: string[]): JsonSchemaNode | undefined {
  if (!schema) return undefined
  const rootRef = schema.discriminator?.mapping?.[kind]
  let cur = deref(schema, rootRef ? { $ref: rootRef } : undefined)
  for (const seg of path) {
    cur = unwrapNullable(schema, cur)
    cur = unwrapNullable(schema, cur?.properties?.[seg])
    if (!cur) return undefined
  }
  return unwrapNullable(schema, cur)
}

/** 约束的简短中文说明：范围与默认值 */
export function constraintHint(node: JsonSchemaNode | undefined): string | undefined {
  if (!node) return undefined
  const parts: string[] = []
  if (node.minimum !== undefined && node.maximum !== undefined) parts.push(`${node.minimum} ~ ${node.maximum}`)
  else if (node.minimum !== undefined) parts.push(`≥ ${node.minimum}`)
  else if (node.maximum !== undefined) parts.push(`≤ ${node.maximum}`)
  else if (node.exclusiveMinimum !== undefined) parts.push(`> ${node.exclusiveMinimum}`)
  if (node.default !== undefined && node.default !== null && node.default !== '') parts.push(`默认 ${String(node.default)}`)
  return parts.length ? parts.join('，') : undefined
}
