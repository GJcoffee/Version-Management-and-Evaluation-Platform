/** 从助手文本里识别图片 / 视频链接：markdown 图片、<img src> / <video src>、以媒体扩展名结尾的裸 URL。 */

export type MediaKind = 'image' | 'video'
/** 链接在原文里的写法：markdown 图片、HTML 标签、裸 URL（裸 URL 的原文本身就留在文字里） */
export type MediaForm = 'markdown' | 'html' | 'bare'

export interface MediaMatch {
  kind: MediaKind
  url: string
  form: MediaForm
  /** 在原文中的区间 [start, end) */
  start: number
  end: number
}

const EXT = 'png|jpe?g|webp|gif|mp4|webm|mov'
const VIDEO_EXT_RE = /\.(mp4|webm|mov)$/i
const MEDIA_EXT_RE = new RegExp(`\\.(${EXT})$`, 'i')
const URL_CHARS = `[^\\s<>"'()\\[\\]，。；：！？、）】》]`

// 三种写法按出现位置从左到右匹配；markdown 图片 / HTML 标签先于裸 URL，避免括号、src 里的 URL 被重复识别
const SCAN_RE = new RegExp(
  [
    String.raw`!\[[^\]]*\]\(\s*([^)\s]+)[^)]*\)`,
    String.raw`<(img|video)\b[^>]*?\bsrc\s*=\s*(?:"([^"]*)"|'([^']*)')[^>]*>`,
    `(https?://${URL_CHARS}+?\\.(?:${EXT})(?:\\?${URL_CHARS}*)?)(?=$|[^\\w./-]|[.,;:!?](?:$|[^\\w./-]))`,
  ].join('|'),
  'gi',
)

/** 去掉路径之后的查询串 / 片段，按扩展名判断类型；不是图片 / 视频扩展名返回 null */
export function mediaKindOf(url: string): MediaKind | null {
  const path = url.split(/[?#]/, 1)[0]
  if (VIDEO_EXT_RE.test(path)) return 'video'
  return MEDIA_EXT_RE.test(path) ? 'image' : null
}

/** 按出现顺序找出文本里的全部图片 / 视频链接 */
export function scanMedia(text: string): MediaMatch[] {
  const out: MediaMatch[] = []
  for (const m of text.matchAll(SCAN_RE)) {
    const start = m.index
    if (m[1] !== undefined) {
      // markdown 图片：扩展名是视频就按视频播放，其余按图片
      out.push({ kind: mediaKindOf(m[1]) ?? 'image', url: m[1], form: 'markdown', start, end: start + m[0].length })
    } else if (m[2] !== undefined) {
      out.push({ kind: m[2].toLowerCase() === 'video' ? 'video' : 'image', url: m[3] ?? m[4], form: 'html', start, end: start + m[0].length })
    } else {
      // 查询串末尾的标点不属于链接
      const url = m[5].replace(/[.,;:!?]+$/, '')
      out.push({ kind: mediaKindOf(url) as MediaKind, url, form: 'bare', start, end: start + url.length })
    }
  }
  return out
}
