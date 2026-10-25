import { cn } from './cn'

describe('cn', () => {
  it('合并并过滤假值', () => {
    expect(cn('a', (0 as number) > 1 && 'b', undefined, 'c')).toBe('a c')
  })
  it('后者覆盖冲突的 Tailwind 类', () => {
    expect(cn('px-2 py-1', 'px-4')).toBe('py-1 px-4')
  })
  it('支持对象与数组写法', () => {
    expect(cn(['a', { b: true, c: false }])).toBe('a b')
  })
})
