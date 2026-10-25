import { describe, expect, it } from 'vitest'
import type { ProviderOut } from '@/api/types'
import {
  appendModelNames,
  emptyModelRow,
  toCreateBody,
  toFormState,
  toUpdateBody,
  validateForm,
} from './providerForm'

const provider: ProviderOut = {
  id: 'prv_1',
  name: 'deepseek',
  base_url: 'https://api.deepseek.com/v1',
  has_api_key: true,
  headers: { 'X-Org': 'a' },
  models: [{ name: 'deepseek-chat', context_window: 65536, price_in: 2, price_out: 8 }],
  rpm_limit: 60,
  tpm_limit: null,
  max_concurrency: 8,
  created_at: '2026-10-25T00:00:00.000Z',
  updated_at: '2026-10-25T00:00:00.000Z',
}

describe('validateForm', () => {
  it('新建表单默认值缺名称和 URL', () => {
    const e = validateForm(toFormState())
    expect(e.name).toBeTruthy()
    expect(e.base_url).toBeTruthy()
    expect(e.max_concurrency).toBeUndefined()
  })
  it('校验 URL、数字与模型行', () => {
    const f = toFormState()
    f.name = 'x'
    f.base_url = 'ftp://x'
    f.rpm_limit = '0'
    f.tpm_limit = 'abc'
    f.max_concurrency = '1.5'
    f.models = [
      { ...emptyModelRow(''), price_in: '-1' },
      { ...emptyModelRow('m'), context_window: '1.2' },
      emptyModelRow('m'),
    ]
    const e = validateForm(f)
    expect(Object.keys(e).sort()).toEqual(
      [
        'base_url',
        'max_concurrency',
        'models.0.name',
        'models.0.price_in',
        'models.1.context_window',
        'models.2.name',
        'rpm_limit',
        'tpm_limit',
      ].sort(),
    )
  })
  it('合法表单无错误', () => {
    expect(validateForm(toFormState(provider))).toEqual({})
  })
})

describe('toCreateBody', () => {
  it('空限流为 null，空价格字段省略，不带空密钥', () => {
    const f = toFormState()
    f.name = ' p '
    f.base_url = ' http://127.0.0.1:8000/v1 '
    f.models = [{ ...emptyModelRow('m1'), price_in: '1.5' }]
    const body = toCreateBody(f)
    expect(body).toEqual({
      name: 'p',
      base_url: 'http://127.0.0.1:8000/v1',
      headers: {},
      models: [{ name: 'm1', price_in: 1.5 }],
      rpm_limit: null,
      tpm_limit: null,
      max_concurrency: 8,
    })
    expect('api_key' in body).toBe(false)
    f.api_key = 'sk-1'
    expect(toCreateBody(f).api_key).toBe('sk-1')
  })
})

describe('toUpdateBody', () => {
  it('未修改时为空对象', () => {
    expect(toUpdateBody(toFormState(provider), provider)).toEqual({})
  })
  it('只包含变化的字段', () => {
    const f = toFormState(provider)
    f.base_url = 'https://other/v1'
    f.rpm_limit = ''
    f.headers = { 'X-Org': 'b' }
    expect(toUpdateBody(f, provider)).toEqual({
      base_url: 'https://other/v1',
      rpm_limit: null,
      headers: { 'X-Org': 'b' },
    })
  })
  it('api_key：新值 / 清除 / 不改', () => {
    const f = toFormState(provider)
    expect(toUpdateBody(f, provider).api_key).toBeUndefined()
    expect(toUpdateBody({ ...f, clearKey: true }, provider).api_key).toBe('')
    expect(toUpdateBody({ ...f, api_key: 'sk-new' }, provider).api_key).toBe('sk-new')
    // 本来就没有密钥时，"清除"不产生请求字段
    expect(toUpdateBody({ ...f, clearKey: true }, { ...provider, has_api_key: false }).api_key).toBeUndefined()
  })
  it('models 变化时整体替换', () => {
    const f = toFormState(provider)
    f.models = appendModelNames(f.models, ['deepseek-reasoner'])
    expect(toUpdateBody(f, provider).models).toEqual([
      { name: 'deepseek-chat', context_window: 65536, price_in: 2, price_out: 8 },
      { name: 'deepseek-reasoner' },
    ])
  })
})

describe('appendModelNames', () => {
  it('忽略已存在与重复的名称', () => {
    const rows = [emptyModelRow('a')]
    const next = appendModelNames(rows, ['a', 'b', 'b', ''])
    expect(next.map((r) => r.name)).toEqual(['a', 'b'])
    expect(appendModelNames(next, ['a', 'b'])).toBe(next)
  })
})
