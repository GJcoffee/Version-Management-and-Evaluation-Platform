import type { AnyAdapterBinding, BindingSnapshot } from '@/api/queries/adapters'
import { ENVIRONMENT_LABEL } from '@/api/queries/adapters'

/** 平台 id：新绑定用 adapter，历史快照用 id。 */
export function bindingAdapter(binding: AnyAdapterBinding): string {
  return 'adapter' in binding ? binding.adapter : binding.id
}

/** 实现大版本：新绑定用 major，历史快照从 version 取首段。 */
export function bindingMajor(binding: AnyAdapterBinding): number {
  if ('major' in binding) return binding.major
  const major = Number(String(binding.version).split('.')[0])
  return Number.isFinite(major) && major >= 1 ? major : 1
}

export const adapterName = (binding: AnyAdapterBinding) =>
  (bindingAdapter(binding) === 'hiagent' ? 'HiAgent' : bindingAdapter(binding))

/** 适配器与关键配置，如「HiAgent · 实现 v1 · 发布版本 47 · app app-test」 */
export function bindingSummary(binding: AnyAdapterBinding): string {
  const { published_version: published, app_id: app } = binding.config
  return [
    `${adapterName(binding)} · 实现 v${bindingMajor(binding)}`,
    published ? `发布版本 ${String(published)}` : null,
    app ? `app ${String(app)}` : null,
  ].filter(Boolean).join(' · ')
}

/** 会话 / 评测标签：「生产 · 存档 #3」「测试 · 存档 #2」；旧的版本绑定快照没有环境 */
export function snapshotLabel(snapshot: BindingSnapshot): string {
  return snapshot.environment
    ? `${ENVIRONMENT_LABEL[snapshot.environment]} · 存档 #${snapshot.revision}`
    : `版本绑定（旧） · 修订 #${snapshot.revision}`
}

/** 悬停说明：适配器与关键配置、存档说明；请求测试却落到生产时说明原因 */
export function snapshotTitle(snapshot: BindingSnapshot): string {
  return [
    snapshot.binding ? bindingSummary(snapshot.binding) : null,
    `说明：${snapshot.reason ?? '无'}`,
    snapshot.requested_environment && snapshot.requested_environment !== snapshot.environment
      ? `请求了${ENVIRONMENT_LABEL[snapshot.requested_environment]}，但测试未配置或与生产相同，已使用生产`
      : null,
  ].filter(Boolean).join('\n')
}
