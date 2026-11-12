import { useId, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { api } from '@/api/client'
import { useAccountSessionRevisions, usePlatformAccounts, type PlatformAccount } from '@/api/queries/accounts'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/Card'
import { Dialog } from '@/components/ui/Dialog'
import { Field, Input } from '@/components/ui/Input'
import { RelativeTime } from '@/components/ui/RelativeTime'
import { LoadingBlock } from '@/components/ui/Spinner'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/Table'
import { formatApiError } from '@/lib/apiError'
import { toast } from '@/stores/toast'

const labels: Record<string, string> = {
  pending: '待检查', active: '可用', disabled: '已停用', check_failed: '检查未完成',
  login_failed: '登录失败', verification_required: '需要额外验证',
}

function AccountEditor({ account, onClose }: { account?: PlatformAccount; onClose: () => void }) {
  const id = useId()
  const qc = useQueryClient()
  const [name, setName] = useState(account?.name ?? '')
  const [origin, setOrigin] = useState(account?.origin ?? '')
  const [tenant, setTenant] = useState(account?.tenant_name ?? '')
  const [username, setUsername] = useState(account?.username ?? '')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const save = async () => {
    setBusy(true)
    setError('')
    try {
      if (account) {
        await api.put(`/adapters/accounts/${account.id}`, {
          body: { name, expected_revision: account.config_revision, ...(password ? { password } : {}) },
        })
      } else {
        await api.post('/adapters/accounts', { body: {
          adapter_id: 'hiagent', name, origin, tenant_name: tenant, username, password,
        } })
      }
      setPassword('')
      void qc.invalidateQueries({ queryKey: ['platform-accounts'] })
      toast.success('账号已保存')
      onClose()
    } catch (err) {
      setError(formatApiError(err, '账号保存失败').message)
    } finally { setBusy(false) }
  }
  return <Dialog open title={account ? '编辑 Trace 账号' : '新增 Trace 账号'} dismissible={!busy}
    onOpenChange={(open) => !open && onClose()}
    description="账号用于获取 Trace，保存后会自动检查登录状态并在到期时重登。"
    footer={<>
      <Button variant="outline" disabled={busy} onClick={onClose}>取消</Button>
      <Button loading={busy} disabled={!name.trim() || !origin.trim() || !username.trim() || (!account && !password)}
        onClick={() => void save()}>保存账号</Button>
    </>}>
    <div className="space-y-3">
      <Field label="账号名称" htmlFor={`${id}-name`} required>
        <Input id={`${id}-name`} disabled={busy} value={name} onChange={(e) => setName(e.target.value)} />
      </Field>
      <Field label="站点地址" htmlFor={`${id}-origin`} required hint="填写站点根地址，与 observe_base_url 一致">
        <Input id={`${id}-origin`} disabled={busy || !!account} placeholder="https://hiagent.example.com"
          value={origin} onChange={(e) => setOrigin(e.target.value)} />
      </Field>
      <Field label="租户名称" htmlFor={`${id}-tenant`} hint="站点登录要求租户时填写">
        <Input id={`${id}-tenant`} disabled={busy || !!account} value={tenant} onChange={(e) => setTenant(e.target.value)} />
      </Field>
      <Field label="用户名" htmlFor={`${id}-username`} required>
        <Input id={`${id}-username`} autoComplete="username" disabled={busy || !!account}
          value={username} onChange={(e) => setUsername(e.target.value)} />
      </Field>
      <Field label="密码" htmlFor={`${id}-password`} required={!account}
        hint={account ? '留空保留原密码；填写新密码后会重新登录' : '加密保存，不会在列表或接口中回显'}>
        <Input id={`${id}-password`} type="password" autoComplete="new-password" disabled={busy}
          value={password} onChange={(e) => setPassword(e.target.value)} />
      </Field>
      {error && <p role="alert" className="text-xs text-danger">{error}</p>}
    </div>
  </Dialog>
}

function SessionImport({ account, onClose }: { account: PlatformAccount; onClose: () => void }) {
  const qc = useQueryClient()
  const id = useId()
  const [headers, setHeaders] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const save = async () => {
    setBusy(true)
    try {
      await api.post(`/adapters/accounts/${account.id}/session`, { body: { headers } })
      setHeaders('')
      void qc.invalidateQueries({ queryKey: ['platform-accounts'] })
      toast.success('登录态已核验并保存')
      onClose()
    } catch (err) {
      setError(formatApiError(err, '登录态检查失败').message)
    } finally { setBusy(false) }
  }
  return <Dialog open title="补充登录态" dismissible={!busy} onOpenChange={(open) => !open && onClose()}
    description={`先在 ${account.origin} 完成登录或额外验证，再填写该账号的 Cookie／CSRF。`}
    footer={<>
      <Button disabled={busy} variant="outline" onClick={onClose}>取消</Button>
      <Button loading={busy} disabled={!headers.trim()} onClick={() => void save()}>核验并保存</Button>
    </>}>
    <Field label="Cookie／CSRF JSON" htmlFor={`${id}-headers`}
      hint={'格式：{"Cookie":"…","X-Csrf-Token":"…"}；只用于当前站点，核验失败不会替换原登录态'}>
      <Input id={`${id}-headers`} type="password" autoComplete="new-password"
        value={headers} disabled={busy} onChange={(e) => setHeaders(e.target.value)} />
    </Field>
    {error && <p role="alert" className="mt-2 text-xs text-danger">{error}</p>}
  </Dialog>
}

function SessionHistory({ account, onClose }: { account: PlatformAccount; onClose: () => void }) {
  const revisions = useAccountSessionRevisions(account.id)
  return <Dialog open title={`${account.name} · 登录态历史`} onOpenChange={(open) => !open && onClose()}
    description="每次 Cookie 更新都会保留修订，历史记录只展示时间和来源。">
    {revisions.isLoading ? <LoadingBlock /> : revisions.isError
      ? <p role="alert" className="text-xs text-danger">登录态历史读取失败</p>
      : !revisions.data?.length ? <p className="text-xs text-muted-foreground">尚无登录态修订</p>
      : <Table>
        <TableHeader><TableRow><TableHead>修订</TableHead><TableHead>来源</TableHead><TableHead>时间</TableHead><TableHead>到期</TableHead></TableRow></TableHeader>
        <TableBody>{revisions.data.map((row) => <TableRow key={row.revision}>
          <TableCell>#{row.revision}{row.current && ' · 当前'}</TableCell>
          <TableCell>{{ login: '重登', check: '检查更新', response: '请求更新', manual: '手动补充' }[row.source] ?? row.source}</TableCell>
          <TableCell><RelativeTime value={row.created_at} /></TableCell>
          <TableCell>{row.expires_at ? <RelativeTime value={row.expires_at} /> : '站点未提供'}</TableCell>
        </TableRow>)}</TableBody>
      </Table>}
  </Dialog>
}

export function PlatformAccountSection() {
  const accounts = usePlatformAccounts()
  const qc = useQueryClient()
  const [dialog, setDialog] = useState<{ type: 'edit' | 'import' | 'history'; account?: PlatformAccount } | null>(null)
  const [busy, setBusy] = useState('')
  const action = async (account: PlatformAccount, operation: 'check' | 'login' | 'toggle') => {
    setBusy(account.id)
    try {
      const result = operation === 'toggle'
        ? await api.put<PlatformAccount>(`/adapters/accounts/${account.id}`, {
          body: { expected_revision: account.config_revision, enabled: !account.enabled },
        })
        : await api.post<PlatformAccount>(`/adapters/accounts/${account.id}/${operation}`)
      void qc.invalidateQueries({ queryKey: ['platform-accounts'] })
      if (result.last_error) toast.error(result.last_error)
      else toast.success(operation === 'toggle' ? (result.enabled ? '账号已启用' : '账号已停用') : '账号登录状态可用')
    } catch (err) {
      toast.error(formatApiError(err, '账号操作失败').message)
    } finally { setBusy('') }
  }
  return <Card>
    <CardHeader><div>
      <CardTitle>Trace 账号</CardTitle>
      <CardDescription>同账号共用最新登录态，每五分钟检查一次，接近到期时提前检查；认证过期会自动重登。API Key 单独维护。</CardDescription>
    </div><Button size="sm" onClick={() => setDialog({ type: 'edit' })}>新增账号</Button></CardHeader>
    <CardContent>
      {accounts.isLoading ? <LoadingBlock /> : accounts.isError
        ? <p role="alert" className="text-sm text-danger">账号列表读取失败</p>
        : !accounts.data?.length ? <p className="text-sm text-muted-foreground">添加账号后，在测试环境配置中选择 Trace 账号，再推送到生产。</p>
        : <Table>
          <TableHeader><TableRow>
            <TableHead>账号</TableHead><TableHead>状态</TableHead><TableHead>最近检查</TableHead><TableHead>操作</TableHead>
          </TableRow></TableHeader>
          <TableBody>{accounts.data.filter((a) => a.adapter_id === 'hiagent').map((account) => <TableRow key={account.id}>
            <TableCell><p className="text-sm font-medium">{account.name}</p>
              <p className="text-xs text-muted-foreground">{account.username}{account.tenant_name && ` · ${account.tenant_name}`}</p>
              <p className="max-w-64 break-all text-xs text-muted-foreground">{account.origin}</p>
            </TableCell>
            <TableCell><Badge variant={account.status === 'active' ? 'success' : 'secondary'}>
              {labels[account.status] ?? account.status}
            </Badge><p className="mt-1 text-xs text-muted-foreground">登录态 #{account.session_revision}</p>
              {account.last_error && <p className="mt-1 max-w-64 text-xs text-danger">{account.last_error}</p>}
            </TableCell>
            <TableCell className="text-xs">{account.last_checked_at ? <RelativeTime value={account.last_checked_at} /> : '尚未检查'}</TableCell>
            <TableCell><div className="flex max-w-80 flex-wrap gap-1">
              <Button size="sm" variant="ghost" disabled={!!busy} onClick={() => setDialog({ type: 'edit', account })}>编辑</Button>
              <Button size="sm" variant="ghost" disabled={!!busy || !account.enabled} onClick={() => void action(account, 'check')}>检查</Button>
              <Button size="sm" variant="ghost" disabled={!!busy || !account.enabled} onClick={() => void action(account, 'login')}>重新登录</Button>
              <Button size="sm" variant="ghost" disabled={!!busy || !account.enabled} onClick={() => setDialog({ type: 'import', account })}>补充登录态</Button>
              <Button size="sm" variant="ghost" disabled={!!busy} onClick={() => setDialog({ type: 'history', account })}>历史</Button>
              <Button size="sm" variant="ghost" disabled={!!busy} onClick={() => void action(account, 'toggle')}>{account.enabled ? '停用' : '启用'}</Button>
            </div>{busy === account.id && <p role="status" className="text-xs text-muted-foreground">正在处理…</p>}</TableCell>
          </TableRow>)}</TableBody>
        </Table>}
      {dialog?.type === 'edit' && <AccountEditor account={dialog.account} onClose={() => setDialog(null)} />}
      {dialog?.type === 'import' && dialog.account && <SessionImport account={dialog.account} onClose={() => setDialog(null)} />}
      {dialog?.type === 'history' && dialog.account && <SessionHistory account={dialog.account} onClose={() => setDialog(null)} />}
    </CardContent>
  </Card>
}
