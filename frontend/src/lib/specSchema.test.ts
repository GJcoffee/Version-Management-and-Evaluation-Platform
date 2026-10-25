import { describe, expect, it } from 'vitest'
import type { SpecSchema } from '@/api/types'
import { constraintHint, schemaAt } from './specSchema'

const schema: SpecSchema = {
  $defs: {
    ConfigSpec: { type: 'object', properties: { model: { $ref: '#/$defs/Model' } } },
    CodeSpec: { type: 'object', properties: { transport: { enum: ['http', 'subprocess'] } } },
    Model: { type: 'object', properties: { params: { $ref: '#/$defs/Params' } } },
    Params: {
      type: 'object',
      properties: { seed: { anyOf: [{ type: 'integer' }, { type: 'null' }], default: null, title: 'Seed' }, top_p: { type: 'number', minimum: 0, maximum: 1, default: 1 } },
    },
  },
  discriminator: { propertyName: 'kind', mapping: { config: '#/$defs/ConfigSpec', code: '#/$defs/CodeSpec' } },
}

describe('schemaAt', () => {
  it('沿 $ref 与可空类型解析', () => {
    expect(schemaAt(schema, 'config', ['model', 'params', 'seed'])?.type).toBe('integer')
    expect(schemaAt(schema, 'config', ['model', 'params', 'top_p'])?.maximum).toBe(1)
    expect(schemaAt(schema, 'code', ['transport'])?.enum).toEqual(['http', 'subprocess'])
  })
  it('路径不存在或 schema 未加载', () => {
    expect(schemaAt(schema, 'config', ['nope'])).toBeUndefined()
    expect(schemaAt(undefined, 'config', ['model'])).toBeUndefined()
  })
  it('constraintHint', () => {
    expect(constraintHint({ type: 'number', minimum: 0, maximum: 1, default: 1 })).toBe('0 ~ 1，默认 1')
    expect(constraintHint({ type: 'integer', minimum: 1 })).toBe('≥ 1')
    expect(constraintHint(undefined)).toBeUndefined()
  })
})
