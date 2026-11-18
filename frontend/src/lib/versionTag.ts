const TAG_RE = /^[A-Za-z0-9][\p{L}\p{N}_.-]{0,63}$/u

export function validateTagName(tag: string): string | null {
  const name = tag.trim()
  if (!name) return '请填写标签'
  if (/^v\p{Nd}+$/u.test(name)) return 'v1、v2 等是自动分配的版本号，请使用其他标签。'
  if (name === 'latest') return 'latest 是保留名称，请填写其他标签。'
  if (!TAG_RE.test(name)) return '标签须以英文字母或数字开头，只能包含字母、数字、下划线、点和连字符，最长 64 字符。'
  return null
}
