/** 极简 CSV 解析（RFC 4180：引号、转义引号、引号内换行、CRLF、BOM），用于导入对话框的列映射预览。 */
export function parseCsv(text: string, maxRows = Infinity): string[][] {
  const src = text.charCodeAt(0) === 0xfeff ? text.slice(1) : text
  const rows: string[][] = []
  let row: string[] = []
  let field = ''
  let inQuotes = false
  for (let i = 0; i < src.length; i++) {
    const ch = src[i]
    if (inQuotes) {
      if (ch === '"') {
        if (src[i + 1] === '"') {
          field += '"'
          i++
        } else inQuotes = false
      } else field += ch
    } else if (ch === '"' && field === '') {
      inQuotes = true
    } else if (ch === ',') {
      row.push(field)
      field = ''
    } else if (ch === '\n' || ch === '\r') {
      if (ch === '\r' && src[i + 1] === '\n') i++
      row.push(field)
      field = ''
      if (row.length > 1 || row[0] !== '') rows.push(row)
      row = []
      if (rows.length >= maxRows) return rows
    } else field += ch
  }
  if (field !== '' || row.length > 0) {
    row.push(field)
    rows.push(row)
  }
  return rows.slice(0, maxRows)
}

export const CSV_FIELDS = ['input', 'case_key', 'expected', 'rubric', 'tags', 'metadata', 'variables', 'tool_mocks'] as const
export type CsvField = (typeof CSV_FIELDS)[number]

export const CSV_FIELD_LABEL: Record<CsvField, string> = {
  input: '输入（input）',
  case_key: '用例标识（case_key）',
  expected: '期望输出（expected）',
  rubric: '评分标准（rubric）',
  tags: '标签（tags，; 分隔）',
  metadata: '元数据（metadata，JSON）',
  variables: '变量（variables，JSON）',
  tool_mocks: '工具 Mock（tool_mocks，JSON）',
}

const ALIASES: Record<CsvField, string[]> = {
  input: ['input', 'question', 'query', 'prompt', '问题', '输入', '用户输入', '提问'],
  case_key: ['case_key', 'key', 'id', '编号', '用例', '用例编号'],
  expected: ['expected', 'answer', 'reference', 'output', '参考答案', '期望', '期望输出', '答案', '标准答案'],
  rubric: ['rubric', 'criteria', '评分标准', '评审标准', '评分细则'],
  tags: ['tags', 'tag', '标签'],
  metadata: ['metadata', 'meta', '元数据'],
  variables: ['variables', 'vars', '变量'],
  tool_mocks: ['tool_mocks', 'mocks', '工具mock'],
}

/** 映射表：字段 -> 列名（input 可多列表示多轮） */
export type CsvMapping = Record<CsvField, string[]>

export function emptyMapping(): CsvMapping {
  return { input: [], case_key: [], expected: [], rubric: [], tags: [], metadata: [], variables: [], tool_mocks: [] }
}

/** 按列名自动猜测映射（同名或常见中英文别名；标签列允许带括号说明，如“标签(;分隔)”） */
export function guessMapping(columns: string[]): CsvMapping {
  const m = emptyMapping()
  const used = new Set<string>()
  for (const f of CSV_FIELDS) {
    const hit = columns.find((c) => {
      if (used.has(c)) return false
      const n = c.trim().toLowerCase()
      return ALIASES[f].some((a) => n === a || n.startsWith(`${a}(`) || n.startsWith(`${a}（`))
    })
    if (hit) {
      m[f] = [hit]
      used.add(hit)
    }
  }
  return m
}

/** 转为接口的 column_map：input 单列为字符串、多列为数组；未映射的字段省略 */
export function toColumnMap(m: CsvMapping): Record<string, string | string[]> {
  const out: Record<string, string | string[]> = {}
  for (const f of CSV_FIELDS) {
    const cols = m[f].filter(Boolean)
    if (cols.length === 0) continue
    out[f] = f === 'input' && cols.length > 1 ? cols : cols[0]
  }
  return out
}
