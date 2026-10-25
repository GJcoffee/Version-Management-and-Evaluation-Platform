import { useMemo } from 'react'
import { Button } from '@/components/ui/Button'
import { Dialog } from '@/components/ui/Dialog'
import { DiffEditor } from '@/components/ui/DiffEditor'
import { specToYaml } from '@/lib/specYaml'
import type { Draft } from '@/api/types'

/** 草稿冲突（DRAFT_CONFLICT）：左边是服务端版本，右边是本地内容 */
export function ConflictDialog({
  open,
  onOpenChange,
  server,
  localText,
  onOverwrite,
  onDiscard,
}: {
  open: boolean
  onOpenChange: (v: boolean) => void
  server: Draft | null
  localText: string
  onOverwrite: () => void | Promise<unknown>
  onDiscard: () => void | Promise<unknown>
}) {
  const serverText = useMemo(() => (server ? specToYaml(server.spec) : ''), [server])
  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      size="xl"
      dismissible
      title="草稿已被他人修改"
      description={
        server
          ? `服务端当前为 r${server.revision}（${new Date(server.updated_at).toLocaleString()}）。左侧是服务端版本，右侧是你的本地内容。`
          : undefined
      }
      footer={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            稍后处理
          </Button>
          <Button variant="outline" onClick={onDiscard}>
            放弃本地修改
          </Button>
          <Button variant="danger" onClick={onOverwrite}>
            用本地内容覆盖
          </Button>
        </>
      }
    >
      <DiffEditor original={serverText} modified={localText} language="yaml" height={420} />
      <p className="mt-2 text-xs text-muted-foreground">
        “覆盖”会把服务端草稿改成右侧内容；“放弃本地修改”会用左侧的服务端内容替换编辑器里的文本。
      </p>
    </Dialog>
  )
}
