import { useRef, useState } from 'react'
import { FileUp } from 'lucide-react'
import { uploadBlob, useCreateRevision } from '@/api/queries/assets'
import type { Asset } from '@/api/types'
import { Button } from '@/components/ui/Button'
import { CodeEditor } from '@/components/ui/CodeEditor'
import { Dialog } from '@/components/ui/Dialog'
import { Field, Input } from '@/components/ui/Input'
import { formatApiError, type FormattedError } from '@/lib/apiError'
import { formatBytes } from '@/lib/format'
import { toast } from '@/stores/toast'
import { contentTypeForFile, languageFor } from './assetUtils'

/** 新建修订（prompt / tool / knowledge / file）：编辑内容或上传文件，填写提交说明 */
export function NewRevisionDialog({
  asset,
  baseContent,
  baseContentType,
  onClose,
  onCreated,
}: {
  asset: Asset
  /** 基于哪个修订的内容开始编辑；null 表示不可编辑（二进制） */
  baseContent: string | null
  baseContentType?: string
  onClose: () => void
  onCreated: (revisionId: string) => void
}) {
  const create = useCreateRevision()
  const fileRef = useRef<HTMLInputElement>(null)
  const canEditText = baseContent !== null
  const allowUpload = asset.kind === 'knowledge' || asset.kind === 'file'
  const [mode, setMode] = useState<'text' | 'file'>(canEditText ? 'text' : 'file')
  const [content, setContent] = useState(baseContent ?? '')
  const [file, setFile] = useState<File | null>(null)
  const [message, setMessage] = useState('')
  const [error, setError] = useState<FormattedError | null>(null)
  const [busy, setBusy] = useState(false)

  const submit = async () => {
    setError(null)
    if (mode === 'file' && !file) return setError({ message: '请选择文件', lines: [] })
    if (mode === 'text' && !content.trim()) return setError({ message: '内容不能为空', lines: [] })
    setBusy(true)
    try {
      const body =
        mode === 'file' && file
          ? { blob_upload_id: (await uploadBlob(file)).blob_upload_id, content_type: contentTypeForFile(file.name) }
          : { content, content_type: baseContentType }
      const r = await create.mutateAsync({ id: asset.id, body: { ...body, message: message.trim() || undefined } })
      if (r.existed) toast.warning(`内容与 r${r.revision.rev} 相同，未创建新修订`, { description: '当前生效的修订已指向它' })
      else toast.success(`已创建修订 r${r.revision.rev}`)
      onClose()
      onCreated(r.revision.id)
    } catch (e) {
      setError(formatApiError(e, '创建修订失败'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog
      open
      onOpenChange={(v) => !busy && !v && onClose()}
      size="lg"
      title={`新建修订：${asset.name}`}
      description="修订不可修改；保存后会成为新的 @latest。内容与已有修订相同时不会新增。"
      footer={
        <>
          <Button variant="outline" onClick={onClose} disabled={busy}>
            取消
          </Button>
          <Button onClick={submit} loading={busy}>
            保存修订
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        {allowUpload && canEditText && (
          <div className="flex gap-1 text-xs">
            {(['text', 'file'] as const).map((m) => (
              <button
                key={m}
                type="button"
                onClick={() => setMode(m)}
                className={`rounded px-2 py-1 ${mode === m ? 'bg-primary/10 font-medium text-primary' : 'text-muted-foreground hover:text-foreground'}`}
              >
                {m === 'text' ? '编辑文本' : '上传文件'}
              </button>
            ))}
          </div>
        )}
        {mode === 'text' ? (
          <CodeEditor
            value={content}
            onChange={setContent}
            language={languageFor(asset.kind, baseContentType)}
            height={320}
            path={`rev-${asset.id}`}
          />
        ) : (
          <Field label="文件" required>
            <input ref={fileRef} type="file" className="hidden" data-testid="revision-file" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
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
        )}
        <Field label="提交说明" htmlFor="rev-msg">
          <Input id="rev-msg" value={message} onChange={(e) => setMessage(e.target.value)} placeholder="例如：补充退货流程说明" />
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
