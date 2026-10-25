import { describe, expect, it } from 'vitest'
import type { Session } from '@/api/types'
import { sessionCreateBody } from './NewSessionDialog'
import { sessionTitle, sessionTokens, versionLabel } from './sessionUtils'

describe('sessionCreateBody', () => {
  const agent = { id: 'agt_1' }
  it('最新版本 / 标签 / 指定版本分别生成不同的请求体', () => {
    expect(sessionCreateBody('latest', agent, {})).toEqual({ agent: 'agt_1', tag: 'latest' })
    expect(sessionCreateBody('tag:prod', agent, { title: 't' })).toEqual({ agent: 'agt_1', tag: 'prod', title: 't' })
    expect(sessionCreateBody('ver:ver_9', agent, { variables: { a: '1' } })).toEqual({ version_id: 'ver_9', variables: { a: '1' } })
  })
})

describe('sessionUtils', () => {
  it('标题缺省显示“新对话”；token 为输入加输出；版本显示 v{seq}', () => {
    expect(sessionTitle({ title: null })).toBe('新对话')
    expect(sessionTitle({ title: '  ' })).toBe('新对话')
    expect(sessionTitle({ title: '你好' })).toBe('你好')
    expect(sessionTokens({ stats: { prompt_tokens: 10, completion_tokens: 5 } } as Session)).toBe(15)
    expect(sessionTokens({ stats: {} } as Session)).toBe(0)
    expect(versionLabel({ version_seq: 3, version_id: 'ver_abcdef' })).toBe('v3')
    expect(versionLabel({ version_seq: null, version_id: 'ver_abcdef' })).toBe('abcdef')
  })
})
