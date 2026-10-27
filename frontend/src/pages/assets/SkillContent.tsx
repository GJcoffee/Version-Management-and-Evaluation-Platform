import { useMemo, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import { ChevronRight, Download, FileText, Folder, FolderOpen } from 'lucide-react'
import { useSkillFile, useSkillTree } from '@/api/queries/assets'
import type { SkillTree } from '@/api/types'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { CodeEditor } from '@/components/ui/CodeEditor'
import { EmptyState } from '@/components/ui/EmptyState'
import { LoadingBlock } from '@/components/ui/Spinner'
import { cn } from '@/lib/cn'
import { formatBytes } from '@/lib/format'
import { languageForPath } from './assetUtils'

interface TreeNode {
  name: string
  path: string
  directory: boolean
  children: TreeNode[]
}

function fileTree(entries: SkillTree['entries']): TreeNode[] {
  const nodes = new Map<string, TreeNode>()
  const roots: TreeNode[] = []
  for (const entry of [...entries].sort((a, b) => a.path.localeCompare(b.path))) {
    const parts = entry.path.split('/')
    for (let i = 0; i < parts.length; i++) {
      const path = parts.slice(0, i + 1).join('/')
      if (nodes.has(path)) continue
      const node: TreeNode = { name: parts[i], path, directory: i < parts.length - 1 || entry.type === 'directory', children: [] }
      nodes.set(path, node)
      const parent = nodes.get(parts.slice(0, i).join('/'))
      ;(parent ? parent.children : roots).push(node)
    }
  }
  const sort = (items: TreeNode[]) => {
    items.sort((a, b) => Number(b.directory) - Number(a.directory) || a.name.localeCompare(b.name))
    for (const node of items) sort(node.children)
  }
  sort(roots)
  return roots
}

function TreeItems({ nodes, selected, onSelect }: { nodes: TreeNode[]; selected: string | null; onSelect: (path: string) => void }) {
  return (
    <ul className="space-y-0.5">
      {nodes.map((node) => (
        <li key={node.path}>
          {node.directory ? (
            <details open className="group/dir">
              <summary title={node.path} className="flex cursor-pointer list-none items-center gap-1.5 rounded px-1.5 py-1 text-xs hover:bg-accent">
                <ChevronRight className="h-3 w-3 shrink-0 transition-transform group-open/dir:rotate-90" />
                <Folder className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                <span className="truncate">{node.name}</span>
              </summary>
              <div className="ml-3 border-l border-border pl-1.5">
                <TreeItems nodes={node.children} selected={selected} onSelect={onSelect} />
              </div>
            </details>
          ) : (
            <button
              type="button"
              aria-label={node.path}
              aria-pressed={selected === node.path}
              title={node.path}
              onClick={() => onSelect(node.path)}
              className={cn('flex w-full items-center gap-1.5 rounded px-1.5 py-1.5 text-left text-xs', selected === node.path ? 'bg-primary/10 text-primary' : 'hover:bg-accent')}
            >
              <FileText className="ml-3 h-3.5 w-3.5 shrink-0" />
              <span className="truncate">{node.name}</span>
            </button>
          )}
        </li>
      ))}
    </ul>
  )
}

export function SkillContent({ revisionId, packaged }: { revisionId: string; packaged: boolean }) {
  const tree = useSkillTree(packaged ? revisionId : null)
  const [picked, setPicked] = useState<string | null>(null)
  const [source, setSource] = useState(false)
  const selected = picked ?? tree.data?.default_path ?? null
  const file = useSkillFile(revisionId, selected)
  const nodes = useMemo(() => fileTree(tree.data?.entries ?? []), [tree.data])
  const downloadUrl = `/api/v1/assets/revisions/${revisionId}/skill/file?path=${encodeURIComponent(selected ?? '')}&download=true`
  if (!packaged) {
    return (
      <EmptyState
        icon={FolderOpen}
        title="缺少 Skill 原始文件"
        description="这个修订只保存了导出元数据，无法还原文件和目录。请从源平台重新导出完整 Skill 文件夹或 ZIP。"
        action={<a href={`/api/v1/assets/revisions/${revisionId}/raw`} download><Button size="sm" variant="outline">下载原始元数据</Button></a>}
      />
    )
  }
  if (tree.isLoading) return <LoadingBlock />
  if (tree.error || !tree.data) return <p role="alert" className="p-4 text-sm text-danger">文件目录加载失败，请重试。</p>
  return (
    <div className="overflow-hidden rounded-md border border-border">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border bg-muted/30 px-3 py-2">
        <span className="text-xs text-muted-foreground">{tree.data.file_count} 个文件 · 保留原始目录结构</span>
        <a href={`/api/v1/assets/revisions/${revisionId}/raw`} download className="flex items-center gap-1.5 text-xs text-primary hover:underline">
          <Download className="h-3.5 w-3.5" />下载完整 Skill
        </a>
      </div>
      <div className="grid min-h-[440px] grid-cols-1 md:grid-cols-[14rem_minmax(0,1fr)]">
        <nav aria-label="Skill 文件目录" className="max-h-[560px] overflow-auto border-b border-border bg-muted/20 p-2 md:border-b-0 md:border-r">
          <TreeItems nodes={nodes} selected={selected} onSelect={(path) => { setPicked(path); setSource(false) }} />
        </nav>
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2 border-b border-border px-3 py-2">
            <span title={selected ?? ''} className="min-w-0 flex-1 break-all font-mono text-xs">{selected}</span>
            <Badge variant="secondary">只读</Badge>
            {selected && <a href={downloadUrl} download aria-label="下载当前文件" className="text-muted-foreground hover:text-primary"><Download className="h-3.5 w-3.5" /></a>}
          </div>
          {file.isLoading ? <LoadingBlock /> : file.error ? <p role="alert" className="p-4 text-sm text-danger">文件加载失败</p> : file.data?.content_available ? (
            <>
              {selected?.toLowerCase().endsWith('.md') && (
                <div className="flex gap-1 border-b border-border px-3 py-1 text-xs">
                  <button className={cn('rounded px-2 py-1', !source && 'bg-accent font-medium')} onClick={() => setSource(false)}>预览</button>
                  <button className={cn('rounded px-2 py-1', source && 'bg-accent font-medium')} onClick={() => setSource(true)}>源文件</button>
                </div>
              )}
              {!source && selected?.toLowerCase().endsWith('.md') ? (
                <div className="md-body max-h-[480px] overflow-auto p-5">
                  <ReactMarkdown components={{ img: ({ alt }) => <span>{alt}</span>, a: ({ children, href }) => <a href={href?.startsWith('https://') ? href : undefined} target="_blank" rel="noreferrer">{children}</a> }}>
                    {(file.data.content ?? '').replace(/^---\r?\n[\s\S]*?\r?\n---(?:\r?\n|$)/, '')}
                  </ReactMarkdown>
                </div>
              ) : (
                <CodeEditor value={file.data.content ?? ''} readOnly language={languageForPath(selected ?? '')} height={440} path={`skill-${revisionId}-${selected}`} className="rounded-none border-0" />
              )}
            </>
          ) : (
            <EmptyState title="此文件无法预览" description={`${formatBytes(file.data?.size ?? 0)} · 二进制文件或内容超过预览限制`} action={<a href={downloadUrl} download><Button size="sm" variant="outline">下载原文件</Button></a>} />
          )}
        </div>
      </div>
    </div>
  )
}
