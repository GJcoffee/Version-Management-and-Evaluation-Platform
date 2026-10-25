import { MaintenanceSection } from './settings/MaintenanceSection'
import { ProvidersSection } from './settings/ProvidersSection'
import { SchedulerSection } from './settings/SchedulerSection'

/** 设置：Provider、调度设置、数据清理 */
export default function SettingsPage() {
  return (
    <div className="mx-auto max-w-5xl space-y-4 p-6">
      <h1 className="text-lg font-semibold">设置</h1>
      <ProvidersSection />
      <SchedulerSection />
      <MaintenanceSection />
    </div>
  )
}
