import { describe, expect, it } from 'vitest'
import type { VersionSummary } from '@/api/types'
import { milestoneText, selectableVersions, versionOptionText } from './versionArchive'

const v = (seq: number, extra: Partial<VersionSummary> = {}) =>
  ({ id: `ver_${seq}`, seq, tags: [], milestone: false, milestone_title: null, archived_into: null, ...extra }) as VersionSummary

describe('版本归档的展示与筛选', () => {
  it('里程碑标识带阶段名，没有阶段名时用「阶段性成果」', () => {
    expect(milestoneText(v(3, { milestone: true, milestone_title: '第一阶段：语气优化' }))).toBe('★ 第一阶段：语气优化')
    expect(milestoneText(v(3, { milestone: true }))).toBe('★ 阶段性成果')
    expect(milestoneText(v(2))).toBe('')
  })

  it('选择器文字依次拼接标签与里程碑标识', () => {
    expect(versionOptionText(v(4, { tags: ['prod', 'old'], milestone: true, milestone_title: '一' }), ['最新'])).toBe(
      'v4 · prod,old · ★ 一 · 最新',
    )
    expect(versionOptionText(v(1))).toBe('v1')
  })

  const all = [v(4, { milestone: true }), v(3, { archived_into: 'ver_4' }), v(2, { archived_into: 'ver_4' }), v(5)]

  it('默认隐藏已归档的，里程碑与未归档的保留；已选中的已归档版本不消失', () => {
    expect(selectableVersions(all, false).map((x) => x.seq)).toEqual([4, 5])
    expect(selectableVersions(all, false, ['ver_2']).map((x) => x.seq)).toEqual([4, 2, 5])
    expect(selectableVersions(all, true).map((x) => x.seq)).toEqual([4, 3, 2, 5])
  })
})
