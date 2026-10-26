import { mediaKindOf, scanMedia, type MediaMatch } from './media'

/** mdast 节点的最小描述（只用到本插件读写的字段） */
interface MdNode {
  type: string
  value?: string
  url?: string
  alt?: string | null
  children?: MdNode[]
  data?: { hName: string; hProperties: Record<string, unknown> }
}

// 链接文字里的 URL 本来就可点击，不再展开
const SKIP_CHILDREN = new Set(['link', 'linkReference'])
const CLOSING_TAG_RE = /^<\/(img|video)\s*>$/i

function mediaNode(kind: 'image' | 'video', url: string, alt: string, bare: boolean): MdNode {
  // 自定义节点类型 + data.hName/hProperties：交给 mdast-util-to-hast 转成 <img> / <video>，由 Markdown 组件渲染
  const props: Record<string, unknown> = { src: url }
  if (kind === 'image') props.alt = alt
  if (bare) props.dataBare = true
  return { type: 'mediaEmbed', data: { hName: kind === 'video' ? 'video' : 'img', hProperties: props } }
}

function fromMatch(m: MediaMatch): MdNode {
  return mediaNode(m.kind, m.url, '', m.form === 'bare')
}

function visit(node: MdNode): void {
  const children = node.children
  if (!children) return
  const next: MdNode[] = []
  for (const child of children) {
    if (child.type === 'image' && child.url && mediaKindOf(child.url) === 'video') {
      next.push(mediaNode('video', child.url, '', false)) // ![](x.mp4)
    } else if (child.type === 'text' && child.value) {
      const found = scanMedia(child.value)
      if (found.length === 0) {
        next.push(child)
        continue
      }
      // 文字原样保留（链接仍可见、可复制），媒体紧跟在对应链接之后
      let pos = 0
      for (const m of found) {
        next.push({ type: 'text', value: child.value.slice(pos, m.end) }, fromMatch(m))
        pos = m.end
      }
      if (pos < child.value.length) next.push({ type: 'text', value: child.value.slice(pos) })
    } else if (child.type === 'html' && child.value) {
      const found = scanMedia(child.value).filter((m) => m.form === 'html')
      if (found.length > 0) next.push(...found.map(fromMatch))
      else if (!CLOSING_TAG_RE.test(child.value.trim())) next.push(child)
    } else {
      if (!SKIP_CHILDREN.has(child.type)) visit(child)
      next.push(child)
    }
  }
  node.children = next
}

/** remark 插件：把 <img> / <video> 标签和裸媒体 URL 变成图片 / 视频节点（react-markdown 默认不渲染原始 HTML，也不识别裸 URL） */
export function remarkMedia() {
  return (tree: MdNode) => visit(tree)
}
