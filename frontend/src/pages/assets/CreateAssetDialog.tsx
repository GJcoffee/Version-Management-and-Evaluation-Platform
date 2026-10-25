import { useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { FileUp } from 'lucide-react'
import { uploadBlob, useCreateAsset } from '@/api/queries/assets'
import type { AssetKind } from '@/api/types'
import { Button } from '@/components/ui/Button'
import { CodeEditor } from '@/components/ui/CodeEditor'
import { Dialog } from '@/components/ui/Dialog'
import { Field, Input } from '@/components/ui/Input'
import { Select } from '@/components/ui/Select'
import { ASSET_KIND_LABEL } from '@/lib/assetRef'
import { formatApiError, type FormattedError } from '@/lib/apiError'
import { formatBytes } from '@/lib/format'
import { toast } from '@/stores/toast'
import { PROMPT_TEMPLATE, TOOL_TEMPLATES, contentTypeForFile, languageFor, validateAssetName } from './assetUtils'

export interface CreateAssetDialogProps {
  open: boolean
  onOpenChange: (v: boolean) => void
  defaultKind: AssetKind
  /** kind=dataset 时由页面打开导入对话框 */
  onDataset: () => void
}

export function CreateAssetDialog(props: CreateAssetDialogProps) {
  if (!props.open) return null
  return <Inner {...props} />
}

type KnowledgeMode = 'text' | 'file'

function Inner({ onOpenChange, defaultKind, onDataset }: CreateAssetDialogProps) {
  const navigate = useNavigate()
  const create = useCreateAsset()
  const fileRef = useRef<HTMLInputElement>(null)
  const [kind, setKind] = useState<AssetKind>(defaultKind === 'dataset' ? 'prompt' : defaultKind)
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [message, setMessage] = useState('')
  const [content, setContent] = useState(() => (defaultKind === 'tool' ? TOOL_TEMPLATES.http.content : defaultKind === 'prompt' ? PROMPT_TEMPLATE : ''))
  const [mode, setMode] = useState<KnowledgeMode>('text')
  const [file, setFile] = useState<File | null>(null)
  const [touched, setTouched] = useState(false)
  const [error, setError] = useState<FormattedError | null>(null)
  const [busy, setBusy] = useState(false)

  const changeKind = (k: AssetKind) => {
    if (k === 'dataset') {
      onOpenChange(false)
      onDataset()
      return
    }
    setKind(k)
    setError(null)
    setContent(k === 'tool' ? TOOL_TEMPLATES.http.content : k === 'prompt' ? PROMPT_TEMPLATE : '')
  }

  const useFile = kind === 'file' || (kind === 'knowledge' && mode === 'file')
  const nameError = validateAssetName(name)
  const contentError = useFile ? (file ? null : '请选择文件') : content.trim() ? null : '内容不能为空'

  const submit = async () => {
    setTouched(true)
    if (nameError || contentError) return
    setError(null)
    setBusy(true)
    try {
      const body = { kind, name, description: description.trim() || undefined, message: message.trim() || undefined }
      const r = await create.mutateAsync(
        useFile && file
          ? { ...body, blob_upload_id: (await uploadBlob(file)).blob_upload_id, content_type: contentTypeForFile(file.name) }
          : { ...body, content },
      )
      toast.success(r.created ? `已创建 ${kind}/${name}` : `${name} 已存在，已追加修订 r${r.revision.rev}${r.existed ? '（内容未变）' : ''}`)
      onOpenChange(false)
      navigate(`/assets/${r.asset.id}`)
    } catch (e) {
      setError(formatApiError(e, '创建失败'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog
      open
      onOpenChange={(v) => !busy && onOpenChange(v)}
      size="lg"
      title="新建资产"
      footer={
        <>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={busy}>
            取消
          </Button>
          <Button onClick={submit} loading={busy}>
            创建
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <div className="grid grid-cols-[10rem_1fr] gap-3">
          <Field label="类型" htmlFor="as-kind">
            <Select id="as-kind" value={kind} onChange={(e) => changeKind(e.target.value as AssetKind)}>
              {(['prompt', 'tool', 'knowledge', 'dataset', 'file'] as AssetKind[]).map((k) => (
                <option key={k} value={k}>
                  {ASSET_KIND_LABEL[k]}（{k}）
                </option>
              ))}
            </Select>
          </Field>
          <Field
            label="名称"
            required
            htmlFor="as-name"
            error={touched ? nameError : null}
            hint="小写字母、数字、连字符；同名资产已存在时会追加一个新修订"
          >
            <Input id="as-name" mono value={name} invalid={touched && !!nameError} onChange={(e) => setName(e.target.value)} />
          </Field>
        </div>
        <Field label="描述" htmlFor="as-desc">
          <Input id="as-desc" value={description} onChange={(e) => setDescription(e.target.value)} />
        </Field>

        {kind === 'knowledge' && (
          <div className="flex gap-1 text-xs">
            {(['text', 'file'] as const).map((m) => (
              <button
                key={m}
                type="button"
                onClick={() => setMode(m)}
                className={`rounded px-2 py-1 ${mode === m ? 'bg-primary/10 font-medium text-primary' : 'text-muted-foreground hover:text-foreground'}`}
              >
                {m === 'text' ? '输入文本' : '上传文件（md / txt / jsonl）'}
              </button>
            ))}
          </div>
        )}

        {useFile ? (
          <Field label="文件" required error={touched ? contentError : null}>
            <input
              ref={fileRef}
              type="file"
              className="hidden"
              data-testid="asset-file"
              accept={kind === 'knowledge' ? '.md,.markdown,.txt,.jsonl,.ndjson' : undefined}
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            />
            <div className="flex items-center gap-3">
              <Button variant="outline" size="sm" onClick={() => fileRef.current?.click()}>
                <FileUp className="h-3.5 w-3.5" />
                选择文件
              </Button>
              {file && (
                <span className="text-sm">
                  {file.name} <span className="text-xs text-muted-foreground">{formatBytes(file.size)}</span>
                </span>
              )}
            </div>
          </Field>
        ) : (
          <Field label={kind === 'tool' ? '工具定义（YAML / JSON）' : '内容'} required error={touched ? contentError : null}>
            {kind === 'tool' && (
              <div className="mb-1 flex gap-1 text-xs">
                <span className="py-1 text-muted-foreground">示例模板：</span>
                {Object.entries(TOOL_TEMPLATES).map(([k, t]) => (
                  <button
                    key={k}
                    type="button"
                    onClick={() => setContent(t.content)}
                    className="rounded px-2 py-1 text-primary hover:bg-primary/10"
                  >
                    {t.label}
                  </button>
                ))}
              </div>
            )}
            <CodeEditor
              value={content}
              onChange={setContent}
              language={languageFor(kind)}
              height={280}
              path={`create-${kind}`}
            />
          </Field>
        )}

        <Field label="提交说明" htmlFor="as-msg">
          <Input id="as-msg" value={message} onChange={(e) => setMessage(e.target.value)} placeholder="初版" />
        </Field>

        {error && (
          <div role="alert" className="rounded-md border border-danger/40 bg-danger/10 p-2.5 text-xs text-danger">
            <p className="font-medium">{error.message}</p>
            {error.lines.length > 0 && (
              <ul className="mt-1 list-inside list-disc space-y-0.5">
                {error.lines.map((l, i) => (
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
