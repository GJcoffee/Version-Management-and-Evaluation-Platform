import { describe, expect, it } from 'vitest'
import { contentTypeForFile, languageFor, suggestAssetName, validateAssetName } from './assetUtils'

describe('assetUtils', () => {
  it('资产名校验', () => {
    expect(validateAssetName('cs-system')).toBeNull()
    expect(validateAssetName('a')).toBeNull()
    expect(validateAssetName('')).toBeTruthy()
    expect(validateAssetName('Bad_Name')).toBeTruthy()
    expect(validateAssetName('-x')).toBeTruthy()
    expect(validateAssetName('x-')).toBeTruthy()
    expect(validateAssetName('a'.repeat(65))).toBeTruthy()
  })
  it('由文件名生成合法资产名', () => {
    expect(suggestAssetName('My Cases_v2.jsonl')).toBe('my-cases-v2')
    expect(suggestAssetName('文档.md')).toBe('') // 全中文得到空串，需用户自填
    expect(suggestAssetName('FAQ 文档.md')).toBe('faq')
    expect(suggestAssetName('faq.md')).toBe('faq')
  })
  it('content_type 与语言', () => {
    expect(contentTypeForFile('a.jsonl')).toBe('application/jsonl')
    expect(contentTypeForFile('a.md')).toBe('text/markdown')
    expect(contentTypeForFile('a.bin')).toBeUndefined()
    expect(languageFor('tool')).toBe('yaml')
    expect(languageFor('tool', 'application/json')).toBe('json')
    expect(languageFor('prompt')).toBe('markdown')
    expect(languageFor('file', 'text/plain')).toBe('plaintext')
  })
})
