import { useId, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { api } from '@/api/client'
import { useTraceKeyConnections, type TraceKeyConnection } from '@/api/queries/keyConnections'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/Card'
import { Dialog } from '@/components/ui/Dialog'
import { Field, Input } from '@/components/ui/Input'
import { RelativeTime } from '@/components/ui/RelativeTime'
import { Select } from '@/components/ui/Select'
import { LoadingBlock } from '@/components/ui/Spinner'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/Table'
import { formatApiError } from '@/lib/apiError'
import { toast } from '@/stores/toast'

function KeyEditor({ connection, onClose }: { connection?: TraceKeyConnection; onClose: () => void }) {
  const id = useId()
  const qc = useQueryClient()
  const [name, setName] = useState(connection?.name ?? '')
  const [origin, setOrigin] = useState(connection?.origin ?? '')
  const [topUrl, setTopUrl] = useState(connection?.top_url ?? '')
  const [workspace, setWorkspace] = useState(connection?.workspace_id ?? '')
  const [source, setSource] = useState<'key' | 'hicli'>(connection?.credential_source ?? 'key')
  const [ak, setAk] = useState('')
  const [sk, setSk] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const validKeys = source === 'hicli' || (connection && !ak && !sk) || (ak.trim() && sk.trim())
  const save = async () => {
    setBusy(true)
    setError('')
    try {
      const keys = source === 'key' && ak && sk ? { access_key_id: ak, access_key_secret: sk } : {}
      const result = connection
        ? await api.put<TraceKeyConnection>(`/adapters/key-connections/${connection.id}`, {
          body: { name, expected_revision: connection.revision, ...keys },
        })
        : await api.post<TraceKeyConnection>('/adapters/key-connections', { body: {
          name, origin, top_url: topUrl, workspace_id: workspace, credential_source: source, ...keys,
        } })
      setAk('')
      setSk('')
      void qc.invalidateQueries({ queryKey: ['trace-key-connections'] })
      if (result.status === 'active') toast.success('密钥连接已保存，认证检查通过')
      else toast.error(result.last_error ?? '连接已保存，认证检查未通过，请编辑后重试')
      onClose()
    } catch (err) {
      setError(formatApiError(err, '密钥连接保存失败').message)
    } finally { setBusy(false) }
  }
  return <Dialog open title={connection ? '编辑密钥连接' : '新建密钥连接'} dismissible={!busy}
    onOpenChange={(open) => !open && onClose()}
    description="使用 HiAgent 账号中心的访问密钥查询执行链路；保存后自动检查。应用调用密钥在下方连接配置中单独维护。"
    footer={<>
      <Button variant="outline" disabled={busy} onClick={onClose}>取消</Button>
      <Button loading={busy} disabled={!name.trim() || !origin.trim() || !topUrl.trim() || !workspace.trim() || !validKeys}
        onClick={() => void save()}>保存并检查</Button>
    </>}>
    <div className="space-y-3">
      <Field label="连接名称" htmlFor={`${id}-name`} required hint="自定义，用于区分">
        <Input id={`${id}-name`} disabled={busy} value={name} onChange={(e) => setName(e.target.value)} />
      </Field>
      <Field label="站点地址" htmlFor={`${id}-origin`} required hint="登录后网页的根地址">
        <Input id={`${id}-origin`} disabled={busy || !!connection} placeholder="https://hiagent.example.com"
        value={origin} onChange={(e) => setOrigin(e.target.value)} />
      </Field>
      <Field label="密钥接口地址" htmlFor={`${id}-top`} required hint="平台密钥签名接口">
        <Input id={`${id}-top`} disabled={busy || !!connection} placeholder="https://hiagent.example.com:30040"
          value={topUrl} onChange={(e) => setTopUrl(e.target.value)} />
      </Field>
      <Field label="工作空间编号" htmlFor={`${id}-workspace`} required hint="原链接中/workspace/后面的那一段">
        <Input id={`${id}-workspace`} disabled={busy || !!connection} value={workspace} onChange={(e) => setWorkspace(e.target.value)} />
      </Field>
      <Field label="密钥来源" htmlFor={`${id}-source`}>
        <Select id={`${id}-source`} disabled={busy || !!connection} value={source}
          onChange={(e) => { setSource(e.target.value as 'key' | 'hicli'); setAk(''); setSk('') }}>
          <option value="key">填写账号密钥，加密保存</option>
          <option value="hicli">复用本机已配置的密钥</option>
        </Select>
      </Field>
      {source === 'key' ? <>
        <Field label="访问密钥标识" htmlFor={`${id}-ak`} required={!connection}
          hint={connection ? '两项都留空保留原密钥；更换时同时填写' : '账号中心的 AccessKey ID'}>
          <Input id={`${id}-ak`} type="password" autoComplete="new-password" disabled={busy}
            value={ak} onChange={(e) => setAk(e.target.value)} />
        </Field>
        <Field label="访问密钥正文" htmlFor={`${id}-sk`} required={!connection} hint="账号中心的 AccessKey Secret；加密保存，不会回显">
          <Input id={`${id}-sk`} type="password" autoComplete="new-password" disabled={busy}
            value={sk} onChange={(e) => setSk(e.target.value)} />
        </Field>
      </> : <p className="text-xs text-muted-foreground">使用运行 EvalP 的这台电脑上已配置的 hicli 账号密钥，无需复制正文；更换本机认证会影响此连接。</p>}
      {error && <p role="alert" className="text-xs text-danger">{error}</p>}
    </div>
  </Dialog>
}

export function TraceKeySection() {
  const connections = useTraceKeyConnections()
  const qc = useQueryClient()
  const [editor, setEditor] = useState<{ connection?: TraceKeyConnection } | null>(null)
  const [busy, setBusy] = useState('')
  const action = async (connection: TraceKeyConnection, toggle = false) => {
    setBusy(connection.id)
    try {
      const result = toggle
        ? await api.put<TraceKeyConnection>(`/adapters/key-connections/${connection.id}`, {
          body: { expected_revision: connection.revision, enabled: !connection.enabled },
        })
        : await api.post<TraceKeyConnection>(`/adapters/key-connections/${connection.id}/check`)
      void qc.invalidateQueries({ queryKey: ['trace-key-connections'] })
      if (result.last_error) toast.error(result.last_error)
      else toast.success(result.enabled ? '密钥认证检查通过' : '密钥连接已停用')
    } catch (err) { toast.error(formatApiError(err, '密钥连接操作失败').message) }
    finally { setBusy('') }
  }
  return <section id="trace-key-connections" className="scroll-mt-4"><Card>
    <CardHeader><div>
      <CardTitle>执行链路密钥连接</CardTitle>
      <CardDescription>用账号访问密钥认证，无需网页登录密码。新建后在测试连接配置中选择，再检查上游认证。</CardDescription>
    </div><Button size="sm" onClick={() => setEditor({})}>新建密钥连接</Button></CardHeader>
    <CardContent>
      {connections.isLoading ? <LoadingBlock /> : connections.isError
        ? <p role="alert" className="text-sm text-danger">密钥连接列表读取失败</p>
        : !connections.data?.length ? <p className="text-sm text-muted-foreground">尚无密钥连接，点击“新建密钥连接”添加。</p>
        : <Table>
          <TableHeader><TableRow><TableHead>连接</TableHead><TableHead>状态</TableHead><TableHead>最近检查</TableHead><TableHead>操作</TableHead></TableRow></TableHeader>
          <TableBody>{connections.data.map((c) => <TableRow key={c.id}>
            <TableCell><p className="text-sm font-medium">{c.name}</p>
              <p className="text-xs text-muted-foreground">{c.credential_source === 'key' ? '平台加密密钥' : '本机已配置密钥'} · 修订 {c.revision}</p>
              <p className="max-w-64 break-all text-xs text-muted-foreground">{c.origin}<br />{c.top_url}<br />{c.workspace_id}</p>
            </TableCell>
            <TableCell><Badge variant={c.status === 'active' ? 'success' : 'secondary'}>
              {({ pending: '待检查', active: '可用', disabled: '已停用', check_failed: '检查未通过' } as Record<string, string>)[c.status] ?? c.status}
            </Badge>{c.last_error && <p className="mt-1 max-w-64 text-xs text-danger">{c.last_error}</p>}</TableCell>
            <TableCell>{c.last_checked_at ? <RelativeTime value={c.last_checked_at} /> : '尚未检查'}</TableCell>
            <TableCell><div className="flex flex-wrap gap-1">
              <Button size="sm" variant="ghost" disabled={!!busy} onClick={() => setEditor({ connection: c })}>编辑</Button>
              <Button size="sm" variant="ghost" disabled={!!busy || !c.enabled} onClick={() => void action(c)}>检查连接</Button>
              <Button size="sm" variant="ghost" disabled={!!busy} onClick={() => void action(c, true)}>{c.enabled ? '停用' : '启用'}</Button>
            </div>{busy === c.id && <p role="status" className="text-xs text-muted-foreground">正在检查…</p>}</TableCell>
          </TableRow>)}</TableBody>
        </Table>}
      {editor && <KeyEditor connection={editor.connection} onClose={() => setEditor(null)} />}
    </CardContent>
  </Card></section>
}
