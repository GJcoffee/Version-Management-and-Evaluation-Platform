import type { AssetKind } from '@/api/types'
import type { CodeLanguage } from '@/components/ui/CodeEditor'

export const ASSET_NAME_RE = /^[a-z0-9]([a-z0-9-]{0,62}[a-z0-9])?$/

/** 资产名：小写字母、数字、连字符，首尾必须是字母或数字，最长 64 */
export function validateAssetName(name: string): string | null {
  if (!name) return '请填写名称'
  if (name.length > 64) return '最长 64 个字符'
  if (!ASSET_NAME_RE.test(name)) return '只允许小写字母、数字、连字符，首尾必须是字母或数字'
  return null
}

/** 把任意文件名转成合法的资产名（用于默认值） */
export function suggestAssetName(filename: string): string {
  const base = filename.replace(/\.[^.]+$/, '')
  const s = base
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 64)
    .replace(/-+$/g, '')
  return s
}

export function languageFor(kind: AssetKind, contentType?: string): CodeLanguage {
  const ct = contentType ?? ''
  if (kind === 'tool') return ct.includes('json') && !ct.includes('yaml') ? 'json' : 'yaml'
  if (kind === 'prompt') return 'markdown'
  if (ct.includes('markdown')) return 'markdown'
  if (ct === 'application/json') return 'json'
  if (ct.includes('yaml')) return 'yaml'
  return 'plaintext'
}

/** 上传文件时按扩展名推断 content_type */
export function contentTypeForFile(filename: string): string | undefined {
  const ext = filename.split('.').pop()?.toLowerCase()
  switch (ext) {
    case 'md':
    case 'markdown':
      return 'text/markdown'
    case 'txt':
      return 'text/plain'
    case 'jsonl':
    case 'ndjson':
      return 'application/jsonl'
    case 'json':
      return 'application/json'
    case 'yaml':
    case 'yml':
      return 'application/x-yaml'
    case 'csv':
      return 'text/csv'
    default:
      return undefined
  }
}

export const TOOL_TEMPLATES: Record<string, { label: string; content: string }> = {
  http: {
    label: 'HTTP 工具',
    content: `name: query_order
description: 根据订单号查询订单状态
parameters:
  type: object
  properties:
    order_id: {type: string, description: 订单号}
  required: [order_id]
impl:
  type: http
  method: POST
  url: http://127.0.0.1:9000/orders/query
  headers: {X-Token: "\${env:ORDER_TOKEN}"}
  body_template: '{"id": "{{ args.order_id }}"}'
  timeout_s: 15
`,
  },
  mock: {
    label: 'Mock 工具',
    content: `name: query_order
description: 根据订单号查询订单状态（模拟实现，用于评测）
parameters:
  type: object
  properties:
    order_id: {type: string, description: 订单号}
  required: [order_id]
impl:
  type: mock
  responses:
    - match: {order_id: "A001"}
      output: {status: 已发货}
    - default: {status: 未找到}
`,
  },
  python: {
    label: 'Python 工具',
    content: `name: query_order
description: 根据订单号查询订单状态
parameters:
  type: object
  properties:
    order_id: {type: string, description: 订单号}
  required: [order_id]
impl:
  type: python
  entry: my_tools.orders:query   # 模块:函数，接收 dict 参数
  # python: /path/to/venv/bin/python   # 可选，默认使用平台自己的解释器
  # workdir: /path/to/project
`,
  },
}

export const PROMPT_TEMPLATE = `你是{{ variables.company }}的客服助手。请礼貌、简洁地回答用户的问题。
`
