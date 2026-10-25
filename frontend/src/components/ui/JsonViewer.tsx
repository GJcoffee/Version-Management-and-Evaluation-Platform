import { useState } from 'react'
import { ChevronDown, ChevronRight } from 'lucide-react'
import { cn } from '@/lib/cn'

export interface JsonViewerProps {
  value: unknown
  /** 默认展开到第几层（根为 0），默认 1 */
  defaultExpandDepth?: number
  className?: string
}

/** 可折叠的 JSON 树，等宽字体 */
export function JsonViewer({ value, defaultExpandDepth = 1, className }: JsonViewerProps) {
  return (
    <div className={cn('overflow-auto rounded-md bg-muted/40 p-2 font-mono text-xs leading-5', className)}>
      <Node name={null} value={value} depth={0} expandDepth={defaultExpandDepth} last />
    </div>
  )
}

function Primitive({ value }: { value: unknown }) {
  if (value === null) return <span className="text-muted-foreground">null</span>
  switch (typeof value) {
    case 'string':
      return <span className="whitespace-pre-wrap break-all text-success">{JSON.stringify(value)}</span>
    case 'number':
      return <span className="text-primary">{String(value)}</span>
    case 'boolean':
      return <span className="text-warning">{String(value)}</span>
    default:
      return <span className="text-muted-foreground">{String(value)}</span>
  }
}

function Node({
  name,
  value,
  depth,
  expandDepth,
  last,
}: {
  name: string | number | null
  value: unknown
  depth: number
  expandDepth: number
  last: boolean
}) {
  const [open, setOpen] = useState(depth < expandDepth)
  const comma = last ? '' : ','
  const label =
    name === null ? null : (
      <>
        <span className="text-foreground">{typeof name === 'number' ? name : JSON.stringify(name)}</span>
        <span className="text-muted-foreground">: </span>
      </>
    )

  const isArray = Array.isArray(value)
  const isObject = value !== null && typeof value === 'object'
  if (!isObject) {
    return (
      <div className="pl-4">
        {label}
        <Primitive value={value} />
        {comma}
      </div>
    )
  }

  const entries: Array<[string | number, unknown]> = isArray
    ? (value as unknown[]).map((v, i) => [i, v])
    : Object.entries(value as Record<string, unknown>)
  const [openCh, closeCh] = isArray ? ['[', ']'] : ['{', '}']

  if (entries.length === 0) {
    return (
      <div className="pl-4">
        {label}
        <span className="text-muted-foreground">
          {openCh}
          {closeCh}
        </span>
        {comma}
      </div>
    )
  }

  return (
    <div className={depth === 0 ? '' : 'pl-4'}>
      <button
        type="button"
        className="-ml-4 inline-flex items-center text-left hover:text-primary"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
      >
        {open ? <ChevronDown className="h-3.5 w-3.5 shrink-0" /> : <ChevronRight className="h-3.5 w-3.5 shrink-0" />}
        <span className="ml-0.5">
          {label}
          <span className="text-muted-foreground">{openCh}</span>
          {!open && (
            <span className="text-muted-foreground">
              {' '}
              {entries.length} {isArray ? '项' : '个键'} {closeCh}
              {comma}
            </span>
          )}
        </span>
      </button>
      {open && (
        <>
          {entries.map(([k, v], i) => (
            <Node
              key={k}
              name={isArray ? null : k}
              value={v}
              depth={depth + 1}
              expandDepth={expandDepth}
              last={i === entries.length - 1}
            />
          ))}
          <div className="text-muted-foreground">
            {closeCh}
            {comma}
          </div>
        </>
      )}
    </div>
  )
}
