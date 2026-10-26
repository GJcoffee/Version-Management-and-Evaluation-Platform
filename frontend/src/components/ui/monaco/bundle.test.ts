import { readFileSync } from 'node:fs'
import path from 'node:path'
import { describe, expect, it } from 'vitest'
import { routes } from '@/routes'

const read = (f: string) => readFileSync(path.resolve(__dirname, f), 'utf8')

describe('Monaco 按需打包守卫', () => {
  it('setup 不整包引入 monaco-editor，也不打包 ts/css/html worker', () => {
    const setup = read('setup.ts')
    expect(setup).not.toMatch(/from 'monaco-editor'/)
    expect(setup).not.toMatch(/ts\.worker|css\.worker|html\.worker/)
    expect(setup).toMatch(/editor\.api/)
  })

  it('features 只注册 yaml / markdown / json 三种语言', () => {
    const imports = [...read('features.ts').matchAll(/^import '(.+)'$/gm)].map((m) => m[1])
    const langs = imports.filter((p) => /languages\/(definitions|features)\//.test(p))
    expect(langs.sort()).toEqual([
      'monaco-editor/languages/definitions/markdown/register',
      'monaco-editor/languages/definitions/yaml/register',
      'monaco-editor/languages/features/json/register',
    ])
  })

  it('CodeEditor 用到的语言都在注册范围内', () => {
    const src = read('../CodeEditor.tsx')
    expect(src).toMatch(/'yaml' \| 'json' \| 'markdown' \| 'plaintext'/)
  })

  it('含编辑器的页面按路由懒加载', () => {
    const lazyType = Symbol.for('react.lazy')
    const flat: Array<{ path?: string; element?: unknown; children?: unknown[] }> = []
    const walk = (rs: Array<{ path?: string; element?: unknown; children?: unknown[] }>) =>
      rs.forEach((r) => {
        flat.push(r)
        if (r.children) walk(r.children as typeof rs)
      })
    walk(routes as never)
    const pick = (p: string) => flat.find((r) => r.path === p)
    for (const p of ['draft/:draftId', 'versions/diff', 'sessions/:id', 'compare/:id']) {
      const el = pick(p)?.element as { type?: { $$typeof?: symbol } } | undefined
      expect(el?.type?.$$typeof, p).toBe(lazyType)
    }
  })
})
