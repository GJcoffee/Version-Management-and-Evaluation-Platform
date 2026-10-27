import { assetTitle } from '@/lib/assetRef'

/**
 * 资产名旁的附注：展示名与资产名不同时显示资产名，有来源 ID 时显示来源 ID。
 * 展示名（来源平台上的原文，可含中文）由调用方用 `assetTitle` 作为主标题。
 */
export function AssetIdentity({
  asset,
  className = 'ml-2',
}: {
  asset: { name: string; display_name?: string | null; source_id?: string | null }
  className?: string
}) {
  const showName = assetTitle(asset) !== asset.name
  if (!showName && !asset.source_id) return null
  return (
    <span className={`${className} text-xs text-muted-foreground`} data-testid="asset-identity">
      {showName && <span className="font-mono">{asset.name}</span>}
      {asset.source_id && (
        <span className="ml-2 font-mono" title="来源平台上的稳定 ID">
          ID {asset.source_id}
        </span>
      )}
    </span>
  )
}
