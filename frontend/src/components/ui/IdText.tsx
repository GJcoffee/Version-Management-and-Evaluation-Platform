import { toast } from '@/stores/toast'
import { cn } from '@/lib/cn'

export interface IdTextProps {
  /** 完整 id 或哈希 */
  value: string
  /** 缩略显示：`prefix_…末 8 位`（ULID 前半部分是时间戳，末尾才有区分度）。默认 true */
  short?: boolean
  className?: string
}

/** 把长 id 缩短：agt_01J8ZQ4K2M... -> agt_…4K2MXYZ1；hash 取前 8 位 */
export function shortenId(value: string): string {
  const m = /^([a-z]{2,4})_(.+)$/.exec(value)
  if (m && m[2].length > 12) return `${m[1]}_…${m[2].slice(-8)}`
  if (!m && value.length > 16) return value.slice(0, 8)
  return value
}

async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    try {
      const ta = document.createElement('textarea')
      ta.value = text
      ta.style.position = 'fixed'
      ta.style.opacity = '0'
      document.body.appendChild(ta)
      ta.select()
      const ok = document.execCommand('copy')
      document.body.removeChild(ta)
      return ok
    } catch {
      return false
    }
  }
}

/** 等宽显示 id/hash，点击复制完整值 */
export function IdText({ value, short = true, className }: IdTextProps) {
  const onCopy = async (e: React.MouseEvent) => {
    e.stopPropagation()
    e.preventDefault()
    if (await copyText(value)) toast.success('已复制', { description: value, duration: 1800 })
    else toast.error('复制失败')
  }
  return (
    <button
      type="button"
      title={`${value}（点击复制）`}
      onClick={onCopy}
      className={cn(
        'inline-block max-w-full cursor-copy truncate rounded bg-muted px-1 py-0.5 align-middle font-mono text-xs text-muted-foreground',
        'hover:bg-accent hover:text-foreground',
        className,
      )}
    >
      {short ? shortenId(value) : value}
    </button>
  )
}
