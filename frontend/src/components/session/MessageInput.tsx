import { useRef, useState, type KeyboardEvent } from 'react'
import { ChevronDown, ChevronRight, SendHorizontal, Square } from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { KeyValueEditor } from '@/components/ui/KeyValueEditor'
import { cn } from '@/lib/cn'

export interface MessageInputProps {
  /**
   * 发送。返回 Promise 时发送期间禁用输入；成功（resolve）后清空输入，失败（reject）保留内容以便重试。
   * variables 仅在“本轮变量”展开且非空时提供。
   */
  onSend: (text: string, opts: { variables?: Record<string, string> }) => void | Promise<void>
  /** 有轮次在运行：显示“停止”按钮，不能发送 */
  running?: boolean
  /** 取消请求进行中 */
  stopping?: boolean
  onStop?: () => void
  disabled?: boolean
  placeholder?: string
  /** 是否提供“本轮变量”编辑，默认 true */
  allowVariables?: boolean
  autoFocus?: boolean
  className?: string
}

/**
 * 多行输入框：Enter 发送，Shift+Enter 换行（输入法组词期间的 Enter 不发送）；
 * 运行中显示“停止”；可展开“本轮变量”（键值对，随本轮一起发送，覆盖会话变量）。
 */
export function MessageInput({
  onSend,
  running,
  stopping,
  onStop,
  disabled,
  placeholder = '输入消息，Enter 发送，Shift+Enter 换行',
  allowVariables = true,
  autoFocus,
  className,
}: MessageInputProps) {
  const [text, setText] = useState('')
  const [vars, setVars] = useState<Record<string, string>>({})
  const [varsOpen, setVarsOpen] = useState(false)
  const [sending, setSending] = useState(false)
  const ref = useRef<HTMLTextAreaElement>(null)
  const varCount = Object.keys(vars).length
  const canSend = !disabled && !running && !sending && text.trim().length > 0

  const send = async () => {
    if (!canSend) return
    const payload = text
    setSending(true)
    try {
      await onSend(payload, varCount > 0 ? { variables: vars } : {})
      setText('')
      requestAnimationFrame(() => ref.current?.focus())
    } catch {
      /* 调用方负责提示错误；保留输入 */
    } finally {
      setSending(false)
    }
  }

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key !== 'Enter' || e.shiftKey) return
    if (e.nativeEvent.isComposing || e.keyCode === 229) return // 输入法选词
    e.preventDefault()
    void send()
  }

  return (
    <div className={cn('rounded-lg border border-input bg-card p-2', className)} data-testid="message-input">
      {allowVariables && varsOpen && (
        <div className="mb-2 rounded-md border border-border bg-muted/30 p-2" data-testid="turn-variables">
          <p className="mb-1.5 text-xs text-muted-foreground">本轮变量（仅对这一轮生效，覆盖会话变量）</p>
          <KeyValueEditor value={vars} onChange={setVars} keyPlaceholder="变量名" valuePlaceholder="值" addLabel="添加变量" />
        </div>
      )}
      <textarea
        ref={ref}
        value={text}
        rows={2}
        autoFocus={autoFocus}
        disabled={disabled}
        placeholder={placeholder}
        aria-label="消息输入"
        onChange={(e) => setText(e.target.value)}
        onKeyDown={onKeyDown}
        className="max-h-48 min-h-[2.75rem] w-full resize-y bg-transparent px-1.5 py-1 text-sm outline-none placeholder:text-muted-foreground disabled:opacity-50"
      />
      <div className="flex items-center justify-between gap-2 pt-1">
        {allowVariables ? (
          <button
            type="button"
            onClick={() => setVarsOpen((o) => !o)}
            aria-expanded={varsOpen}
            className="inline-flex items-center gap-1 rounded px-1 py-0.5 text-xs text-muted-foreground hover:bg-accent hover:text-foreground"
          >
            {varsOpen ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
            本轮变量{varCount > 0 && <span className="font-mono text-primary">（{varCount}）</span>}
          </button>
        ) : (
          <span />
        )}
        {running ? (
          <Button variant="danger" size="md" onClick={onStop} loading={stopping} disabled={!onStop} data-testid="stop-button">
            {!stopping && <Square className="h-3.5 w-3.5 fill-current" />}
            停止
          </Button>
        ) : (
          <Button size="md" onClick={() => void send()} disabled={!canSend} loading={sending} data-testid="send-button">
            {!sending && <SendHorizontal className="h-3.5 w-3.5" />}
            发送
          </Button>
        )}
      </div>
    </div>
  )
}
