import { describe, expect, it } from 'vitest'
import { guessMapping, parseCsv, toColumnMap, emptyMapping } from './csv'

describe('parseCsv', () => {
  it('引号、转义引号、引号内逗号与换行、CRLF、BOM', () => {
    const rows = parseCsv('﻿a,b\r\n"x,1","he said ""hi"""\r\n"多行\n文本",z\r\n')
    expect(rows).toEqual([
      ['a', 'b'],
      ['x,1', 'he said "hi"'],
      ['多行\n文本', 'z'],
    ])
  })
  it('maxRows 限制并忽略空行', () => {
    expect(parseCsv('a\n\nb\nc\nd\n', 2)).toEqual([['a'], ['b']])
  })
  it('末行没有换行', () => {
    expect(parseCsv('a,b\n1,2')).toEqual([['a', 'b'], ['1', '2']])
  })
})

describe('列映射', () => {
  it('按同名和中英文别名猜测（含“标签(;分隔)”）', () => {
    const m = guessMapping(['问题', '参考答案', '标签(;分隔)', 'extra'])
    expect(m.input).toEqual(['问题'])
    expect(m.expected).toEqual(['参考答案'])
    expect(m.tags).toEqual(['标签(;分隔)'])
    expect(m.case_key).toEqual([])
  })
  it('toColumnMap：单列为字符串，多轮 input 为数组，未映射省略', () => {
    const m = emptyMapping()
    m.input = ['q1', 'q2']
    m.expected = ['ans']
    expect(toColumnMap(m)).toEqual({ input: ['q1', 'q2'], expected: 'ans' })
    m.input = ['q1', '']
    expect(toColumnMap(m)).toEqual({ input: 'q1', expected: 'ans' })
  })
})
