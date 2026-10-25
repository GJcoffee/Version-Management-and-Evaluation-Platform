import {
  formatBytes,
  formatCost,
  formatDuration,
  formatPercent,
  formatRelativeTime,
  formatTokens,
} from './format'

describe('formatTokens', () => {
  it('小于 1000 原样输出', () => {
    expect(formatTokens(0)).toBe('0')
    expect(formatTokens(999)).toBe('999')
  })
  it('千位与百万位', () => {
    expect(formatTokens(1234)).toBe('1.2k')
    expect(formatTokens(1000)).toBe('1k')
    expect(formatTokens(1_500_000)).toBe('1.5M')
    expect(formatTokens(2_000_000_000)).toBe('2B')
  })
  it('空值', () => {
    expect(formatTokens(null)).toBe('-')
    expect(formatTokens(undefined)).toBe('-')
    expect(formatTokens(NaN)).toBe('-')
  })
})

describe('formatCost', () => {
  it('保留 4 位小数', () => {
    expect(formatCost(0)).toBe('¥0.0000')
    expect(formatCost(0.0123)).toBe('¥0.0123')
    expect(formatCost(6.12)).toBe('¥6.1200')
    expect(formatCost(0.123456)).toBe('¥0.1235')
  })
  it('空值', () => {
    expect(formatCost(null)).toBe('-')
  })
})

describe('formatDuration', () => {
  it('毫秒/秒/分/时', () => {
    expect(formatDuration(850)).toBe('850ms')
    expect(formatDuration(1234)).toBe('1.2s')
    expect(formatDuration(1000)).toBe('1.0s')
    expect(formatDuration(65_000)).toBe('1m5s')
    expect(formatDuration(3_660_000)).toBe('1h1m')
  })
  it('空值与负数', () => {
    expect(formatDuration(null)).toBe('-')
    expect(formatDuration(-5)).toBe('-')
  })
})

describe('formatBytes', () => {
  it('各单位', () => {
    expect(formatBytes(0)).toBe('0 B')
    expect(formatBytes(1023)).toBe('1023 B')
    expect(formatBytes(1536)).toBe('1.5 KB')
    expect(formatBytes(5 * 1024 * 1024)).toBe('5.0 MB')
    expect(formatBytes(150 * 1024 * 1024)).toBe('150 MB')
    expect(formatBytes(3 * 1024 ** 3)).toBe('3.0 GB')
  })
  it('空值', () => {
    expect(formatBytes(undefined)).toBe('-')
  })
})

describe('formatPercent', () => {
  it('比率转百分比', () => {
    expect(formatPercent(0.8234)).toBe('82.3%')
    expect(formatPercent(1, 0)).toBe('100%')
    expect(formatPercent(null)).toBe('-')
  })
})

describe('formatRelativeTime', () => {
  const now = Date.parse('2026-10-25T08:00:00Z')
  it('各区间', () => {
    expect(formatRelativeTime('2026-10-25T07:59:58Z', now)).toBe('刚刚')
    expect(formatRelativeTime('2026-10-25T07:59:30Z', now)).toBe('30 秒前')
    expect(formatRelativeTime('2026-10-25T07:50:00Z', now)).toBe('10 分钟前')
    expect(formatRelativeTime('2026-10-25T05:00:00Z', now)).toBe('3 小时前')
    expect(formatRelativeTime('2026-10-22T08:00:00Z', now)).toBe('3 天前')
  })
  it('非法输入', () => {
    expect(formatRelativeTime('garbage', now)).toBe('-')
    expect(formatRelativeTime(null, now)).toBe('-')
  })
})
