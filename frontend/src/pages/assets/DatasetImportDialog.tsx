import { useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { AlertTriangle, CheckCircle2, FileUp, Plus, X } from 'lucide-react'
import { useImportDataset } from '@/api/queries/datasets'
import type { DatasetFormat, DatasetImportResult } from '@/api/types'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { Dialog } from '@/components/ui/Dialog'
import { Field, Input } from '@/components/ui/Input'
import { Select } from '@/components/ui/Select'
import {
  CSV_FIELDS,
  CSV_FIELD_LABEL,
  emptyMapping,
  guessMapping,
  parseCsv,
  toColumnMap,
  type CsvField,
  type CsvMapping,
} from '@/lib/csv'
import { formatApiError, type FormattedError } from '@/lib/apiError'
import { formatBytes } from '@/lib/format'
import { suggestAssetName, validateAssetName } from './assetUtils'

export interface DatasetImportDialogProps {
  open: boolean
  onOpenChange: (v: boolean) => void
  /** 给已有数据集新增修订时固定名称 */
  fixedName?: string
  /** 导入成功后（点击“查看数据集”前）的回调 */
  onImported?: (r: DatasetImportResult) => void
}

export function DatasetImportDialog(props: DatasetImportDialogProps) {
  if (!props.open) return null
  return <Inner {...props} />
}

function detectFormat(filename: string): DatasetFormat {
  return filename.toLowerCase().endsWith('.csv') ? 'csv' : 'jsonl'
}

function Inner({ onOpenChange, fixedName, onImported }: DatasetImportDialogProps) {
  const navigate = useNavigate()
  const importer = useImportDataset()
  const fileRef = useRef<HTMLInputElement>(null)
  const [file, setFile] = useState<{ name: string; size: number; text: string } | null>(null)
  const [format, setFormat] = useState<DatasetFormat>('jsonl')
  const [name, setName] = useState(fixedName ?? '')
  const [description, setDescription] = useState('')
  const [message, setMessage] = useState('')
  const [columns, setColumns] = useState<string[]>([])
  const [sampleRows, setSampleRows] = useState<string[][]>([])
  const [mapping, setMapping] = useState<CsvMapping>(emptyMapping())
  const [touched, setTouched] = useState(false)
  const [error, setError] = useState<FormattedError | null>(null)
  const [result, setResult] = useState<DatasetImportResult | null>(null)

  const loadCsv = (text: string) => {
    const rows = parseCsv(text, 4)
    const header = rows[0] ?? []
    setColumns(header)
    setSampleRows(rows.slice(1, 4))
    setMapping(guessMapping(header))
  }

  const onPick = async (f: File | undefined) => {
    if (!f) return
    const text = await f.text()
    const fmt = detectFormat(f.name)
    setFile({ name: f.name, size: f.size, text })
    setFormat(fmt)
    setError(null)
    if (!fixedName && !name) setName(suggestAssetName(f.name))
    if (fmt === 'csv') loadCsv(text)
  }

  const changeFormat = (fmt: DatasetFormat) => {
    setFormat(fmt)
    if (fmt === 'csv' && file) loadCsv(file.text)
  }

  const nameError = fixedName ? null : validateAssetName(name)
  const csvError = format === 'csv' && mapping.input.filter(Boolean).length === 0 ? '请至少选择一个输入列' : null
  const fileError = !file ? '请选择要导入的文件' : null

  const submit = async () => {
    setTouched(true)
    if (nameError || csvError || fileError || !file) return
    setError(null)
    try {
      const r = await importer.mutateAsync({
        name: fixedName ?? name,
        format,
        content: file.text,
        description: description.trim() || undefined,
        message: message.trim() || undefined,
        column_map: format === 'csv' ? toColumnMap(mapping) : undefined,
      })
      setResult(r)
      onImported?.(r)
    } catch (e) {
      setError(formatApiError(e, '导入失败'))
    }
  }

  const setMap = (f: CsvField, idx: number, col: string) =>
    setMapping((m) => ({ ...m, [f]: m[f].length === 0 ? [col] : m[f].map((c, i) => (i === idx ? col : c)) }))

  if (result) {
    return (
      <Dialog
        open
        onOpenChange={onOpenChange}
        title="导入完成"
        footer={
          <>
            <Button variant="outline" onClick={() => onOpenChange(false)}>
              关闭
            </Button>
            <Button
              onClick={() => {
                onOpenChange(false)
                navigate(`/assets/${result.asset.id}`)
              }}
            >
              查看数据集
            </Button>
          </>
        }
      >
        <div className="space-y-3 text-sm" data-testid="import-result">
          <p className="flex items-center gap-2 font-medium text-success">
            <CheckCircle2 className="h-4 w-4" />
            {result.existed
              ? `内容与已有修订 r${result.revision.rev} 相同，未新增修订`
              : result.created
                ? `已创建数据集 ${result.asset.name}`
                : `已为 ${result.asset.name} 新增修订 r${result.revision.rev}`}
          </p>
          <p>
            共解析 <span className="font-mono font-semibold" data-testid="case-count">{result.case_count}</span> 个用例。
          </p>
          {result.warnings.length > 0 ? (
            <div className="rounded-md border border-warning/40 bg-warning/10 p-2.5 text-xs">
              <p className="mb-1 flex items-center gap-1 font-medium text-warning">
                <AlertTriangle className="h-3.5 w-3.5" />
                {result.warnings.length} 类警告
              </p>
              <ul className="space-y-1">
                {result.warnings.map((w, i) => (
                  <li key={i}>
                    <Badge variant="warning" className="mr-1.5 font-mono">
                      {w.code}
                    </Badge>
                    {w.message}
                    {w.count ? `（${w.count} 处` : ''}
                    {w.lines && w.lines.length > 0 ? `，行 ${w.lines.slice(0, 10).join(', ')}${w.lines.length > 10 ? '…' : ''}` : ''}
                    {w.count ? '）' : ''}
                  </li>
                ))}
              </ul>
            </div>
          ) : (
            <p className="text-xs text-muted-foreground">没有警告。</p>
          )}
        </div>
      </Dialog>
    )
  }

  return (
    <Dialog
      open
      onOpenChange={onOpenChange}
      size="lg"
      title={fixedName ? `导入新修订：${fixedName}` : '导入数据集'}
      description="支持 JSONL（每行一个用例）和 CSV（需要列映射）"
      footer={
        <>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button onClick={submit} loading={importer.isPending}>
            导入
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <Field label="文件" required error={touched ? fileError : null}>
          <div className="flex items-center gap-3">
            <input
              ref={fileRef}
              type="file"
              accept=".jsonl,.ndjson,.json,.csv,text/csv"
              className="hidden"
              data-testid="dataset-file"
              onChange={(e) => void onPick(e.target.files?.[0])}
            />
            <Button variant="outline" size="sm" onClick={() => fileRef.current?.click()}>
              <FileUp className="h-3.5 w-3.5" />
              选择文件
            </Button>
            {file ? (
              <span className="text-sm">
                {file.name} <span className="text-xs text-muted-foreground">{formatBytes(file.size)}</span>
              </span>
            ) : (
              <span className="text-xs text-muted-foreground">.jsonl / .csv</span>
            )}
          </div>
        </Field>

        <div className="grid grid-cols-2 gap-3">
          <Field label="数据集名称" required htmlFor="ds-name" error={touched ? nameError : null} hint="同名数据集已存在时，会新增一个修订">
            <Input id="ds-name" mono value={name} disabled={!!fixedName} invalid={touched && !!nameError} onChange={(e) => setName(e.target.value)} />
          </Field>
          <Field label="格式" htmlFor="ds-format">
            <Select id="ds-format" value={format} onChange={(e) => changeFormat(e.target.value as DatasetFormat)}>
              <option value="jsonl">JSONL</option>
              <option value="csv">CSV</option>
            </Select>
          </Field>
        </div>

        {format === 'csv' && file && (
          <div className="space-y-2 rounded-md border border-border p-3">
            <p className="text-sm font-medium">列映射</p>
            {csvError && touched && <p className="text-xs text-danger">{csvError}</p>}
            <div className="grid grid-cols-[11rem_1fr] items-center gap-x-3 gap-y-2">
              {CSV_FIELDS.map((f) => (
                <FieldRow key={f} label={CSV_FIELD_LABEL[f]}>
                  <div className="space-y-1.5">
                    {(mapping[f].length === 0 ? [''] : mapping[f]).map((col, i) => (
                      <div key={i} className="flex items-center gap-1.5">
                        <Select aria-label={CSV_FIELD_LABEL[f]} value={col} onChange={(e) => setMap(f, i, e.target.value)}>
                          <option value="">{f === 'input' ? '请选择列' : '不映射'}</option>
                          {columns.map((c) => (
                            <option key={c} value={c}>
                              {c}
                            </option>
                          ))}
                        </Select>
                        {f === 'input' && i > 0 && (
                          <button
                            type="button"
                            aria-label="移除此轮"
                            className="rounded p-1 text-muted-foreground hover:text-foreground"
                            onClick={() => setMapping((m) => ({ ...m, input: m.input.filter((_, j) => j !== i) }))}
                          >
                            <X className="h-4 w-4" />
                          </button>
                        )}
                      </div>
                    ))}
                    {f === 'input' && (
                      <Button size="sm" variant="ghost" onClick={() => setMapping((m) => ({ ...m, input: [...m.input, ''] }))}>
                        <Plus className="h-3.5 w-3.5" />
                        再加一轮（多轮对话）
                      </Button>
                    )}
                  </div>
                </FieldRow>
              ))}
            </div>
            {sampleRows.length > 0 && (
              <details className="text-xs">
                <summary className="cursor-pointer text-muted-foreground">预览前 {sampleRows.length} 行</summary>
                <div className="mt-1 overflow-x-auto rounded border border-border">
                  <table className="w-full text-xs">
                    <thead className="bg-muted/60">
                      <tr>
                        {columns.map((c) => (
                          <th key={c} className="whitespace-nowrap px-2 py-1 text-left font-medium">
                            {c}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {sampleRows.map((r, i) => (
                        <tr key={i} className="border-t border-border">
                          {columns.map((_, j) => (
                            <td key={j} className="max-w-48 truncate px-2 py-1">
                              {r[j]}
                            </td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </details>
            )}
          </div>
        )}

        <div className="grid grid-cols-2 gap-3">
          <Field label="描述" htmlFor="ds-desc">
            <Input id="ds-desc" value={description} onChange={(e) => setDescription(e.target.value)} />
          </Field>
          <Field label="提交说明" htmlFor="ds-msg">
            <Input id="ds-msg" value={message} onChange={(e) => setMessage(e.target.value)} placeholder="初版" />
          </Field>
        </div>

        {error && (
          <div role="alert" className="rounded-md border border-danger/40 bg-danger/10 p-2.5 text-xs text-danger">
            <p className="font-medium">{error.message}</p>
            {error.lines.length > 0 && (
              <ul className="mt-1 list-inside list-disc space-y-0.5">
                {error.lines.slice(0, 10).map((l, i) => (
                  <li key={i}>{l}</li>
                ))}
              </ul>
            )}
          </div>
        )}
      </div>
    </Dialog>
  )
}

function FieldRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <>
      <span className="text-xs text-muted-foreground">{label}</span>
      {children}
    </>
  )
}
