import { describe, expect, it } from 'vitest'
import type { SettingsValues } from '@/api/types'
import { diffSettings } from './SchedulerSection'

const initial: SettingsValues = {
  'scheduler.workers': 32,
  'scheduler.default_run_concurrency': 8,
  'scheduler.max_attempts': 3,
  'retention.eval_sessions_days': 0,
}
const draftOf = (o: Partial<Record<keyof SettingsValues, string>> = {}) => ({
  'scheduler.workers': '32',
  'scheduler.default_run_concurrency': '8',
  'scheduler.max_attempts': '3',
  'retention.eval_sessions_days': '0',
  ...o,
})

describe('diffSettings', () => {
  it('无修改', () => {
    expect(diffSettings(initial, draftOf())).toEqual({ changed: {}, errors: {} })
  })
  it('只返回变化项（数字化）', () => {
    expect(diffSettings(initial, draftOf({ 'scheduler.workers': '64', 'retention.eval_sessions_days': '30' })).changed).toEqual({
      'scheduler.workers': 64,
      'retention.eval_sessions_days': 30,
    })
  })
  it('校验非法输入', () => {
    const { errors } = diffSettings(
      initial,
      draftOf({ 'scheduler.workers': '0', 'scheduler.max_attempts': '1.5', 'retention.eval_sessions_days': '-1', 'scheduler.default_run_concurrency': '' }),
    )
    expect(Object.keys(errors).sort()).toEqual(
      ['retention.eval_sessions_days', 'scheduler.default_run_concurrency', 'scheduler.max_attempts', 'scheduler.workers'].sort(),
    )
  })
})
