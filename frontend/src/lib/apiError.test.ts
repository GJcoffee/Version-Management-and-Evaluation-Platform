import { describe, expect, it } from 'vitest'
import { ApiError } from '@/api/client'
import { formatApiError } from './apiError'

describe('formatApiError', () => {
  it('列出 details.errors，并去掉主消息里已有的明细', () => {
    const e = new ApiError(400, 'VALIDATION_ERROR', '校验失败', { errors: [{ path: 'content', message: '不合法' }] })
    expect(formatApiError(e)).toEqual({ message: '校验失败', lines: ['content：不合法'] })
    const d = new ApiError(422, 'DATASET_PARSE_ERROR', '解析失败：第 2 行：坏了', { errors: [{ line: 2, message: '坏了' }] })
    expect(formatApiError(d).lines).toEqual([])
  })
  it('非 ApiError', () => {
    expect(formatApiError(new Error('boom')).message).toBe('boom')
    expect(formatApiError('x', '失败').message).toBe('失败')
  })
})
