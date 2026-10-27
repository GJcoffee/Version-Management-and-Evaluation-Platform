import { useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { FileUp, FolderOpen } from 'lucide-react'
import { uploadSkill, useCreateAsset, useCreateRevision } from '@/api/queries/assets'
import type { Asset, SkillUpload } from '@/api/types'
import { Button } from '@/components/ui/Button'
import { Dialog } from '@/components/ui/Dialog'
import { Field, Input } from '@/components/ui/Input'
import { formatApiError } from '@/lib/apiError'
import { formatBytes } from '@/lib/format'
import { toast } from '@/stores/toast'

export function SkillImportDialog({ asset, onClose, onCreated }: { asset?: Asset; onClose: () => void; onCreated?: (id: string) => void }) {
  const navigate = useNavigate()
  const create = useCreateAsset()
  const revise = useCreateRevision()
  const zipRef = useRef<HTMLInputElement>(null)
  const folderRef = useRef<HTMLInputElement>(null)
  const [upload, setUpload] = useState<SkillUpload | null>(null)
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const selectFiles = async (files: File[], folder: boolean) => {
    if (!files.length) return
    setBusy(true)
    setUpload(null)
    setError(null)
    try {
      setUpload(await uploadSkill(files, folder))
    } catch (e) {
      setError(formatApiError(e, 'Skill 导入失败').message)
    } finally {
      setBusy(false)
    }
  }
  const submit = async () => {
    if (!upload) return
    setBusy(true)
    setError(null)
    try {
      const body = { blob_upload_id: upload.blob_upload_id, content_type: 'application/zip', message: message.trim() || undefined }
      const result = asset
        ? await revise.mutateAsync({ id: asset.id, body })
        : await create.mutateAsync({ ...body, kind: 'skill', name: upload.suggested_name })
      toast.success(result.existed ? `内容未变，当前为 r${result.revision.rev}` : `已导入 ${upload.display_name} · r${result.revision.rev}`)
      onClose()
      if (onCreated) onCreated(result.revision.id)
      else navigate(`/assets/${result.asset.id}`)
    } catch (e) {
      setError(formatApiError(e, '保存 Skill 失败').message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <Dialog open onOpenChange={(v) => !v && !busy && onClose()} size="lg"
      title={asset ? `新建修订：${asset.display_name ?? asset.name}` : '导入 Skill'}
      description="选择包含 SKILL.md 的原始文件包。名称、文件内容和目录层级按原样保留。"
      footer={<><Button variant="outline" disabled={busy} onClick={onClose}>取消</Button><Button disabled={!upload} loading={busy} onClick={submit}>{asset ? '保存修订' : '导入'}</Button></>}>
      <div className="space-y-4">
        <input ref={zipRef} type="file" accept=".zip" aria-label="选择 Skill ZIP" className="sr-only" onChange={(e) => { void selectFiles(Array.from(e.target.files ?? []), false); e.target.value = '' }} />
        <input ref={folderRef} type="file" multiple {...{ webkitdirectory: '', directory: '' }} aria-label="选择 Skill 文件夹" className="sr-only" onChange={(e) => { void selectFiles(Array.from(e.target.files ?? []), true); e.target.value = '' }} />
        <div className="flex gap-3">
          <Button variant="outline" disabled={busy} onClick={() => zipRef.current?.click()}><FileUp className="h-4 w-4" />选择 ZIP</Button>
          <Button variant="outline" disabled={busy} onClick={() => folderRef.current?.click()}><FolderOpen className="h-4 w-4" />选择文件夹</Button>
        </div>
        <p className="text-xs text-muted-foreground">ZIP 可完整保留空目录；文件夹导入保留浏览器提供的全部文件路径。</p>
        {upload && <div className="border-l-2 border-primary py-1 pl-3">
          <p className="text-sm font-semibold">{upload.display_name}</p>
          <p className="mt-1 text-xs text-muted-foreground">{upload.file_count} 个文件 · {formatBytes(upload.size)}</p>
        </div>}
        <Field label="提交说明" htmlFor="skill-message"><Input id="skill-message" value={message} onChange={(e) => setMessage(e.target.value)} placeholder="例如：更新参考文档" /></Field>
        {error && <p role="alert" className="text-sm text-danger">{error}</p>}
      </div>
    </Dialog>
  )
}
