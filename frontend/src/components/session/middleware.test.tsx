import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, it, vi } from 'vitest'
import type { MiddlewareSnapshot, Turn } from '@/api/types'
import { mockApi } from '@/test/mockApi'
import { MessageInput } from './MessageInput'
import { MiddlewareTurnDetails } from './MiddlewareTurnDetails'
import { buildSessionState } from './sessionState'
import { mk } from './testEvents'
import { buildStepTree } from './traceTree'

afterEach(() => vi.unstubAllGlobals())

function wrap(ui: React.ReactNode) {
  return <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    {ui}
  </QueryClientProvider>
}

const snapshot: MiddlewareSnapshot = { schema_version: 1, steps: [{
  id: 'json_artifact', version: '1.0.0', source_turn_id: 'source', config: { content_pointer: '/scenes' },
  status: 'error', duration_ms: 12, error: { code: 'ARTIFACT_FETCH_FAILED', message: '链接过期', retriable: false },
}] }

it('首轮禁止开启中间件', () => {
  render(wrap(<MessageInput middlewareSessionId="session" onSend={vi.fn()} />))
  expect(screen.getByRole('button', { name: '轮间中间件' })).toBeDisabled()
})

it('多链接必须选择，发送中间件后下一轮默认关闭', async () => {
  mockApi({ 'GET /api/v1/sessions/session/turns/source/artifact-candidates': {
    source_turn_id: 'source', candidates: [
      { id: 'a', url: 'https://store/a.json', location: '/a' },
      { id: 'b', url: 'https://store/b.json', location: '/b' },
    ],
  } })
  const send = vi.fn().mockResolvedValue(undefined)
  render(wrap(<MessageInput middlewareSessionId="session" sourceTurnId="source" onSend={send} />))
  await userEvent.type(screen.getByLabelText('消息输入'), '不添加对白')
  await userEvent.click(screen.getByRole('button', { name: '轮间中间件' }))
  const candidates = await screen.findByLabelText('JSON 产物')
  expect(screen.getByTestId('send-button')).toBeDisabled()
  fireEvent.change(candidates, { target: { value: 'b' } })
  await waitFor(() => expect(screen.getByTestId('send-button')).toBeEnabled())
  await userEvent.click(screen.getByTestId('send-button'))
  await waitFor(() => expect(send).toHaveBeenCalledOnce())
  expect(send.mock.calls[0][1].middlewares[0]).toMatchObject({
    source_turn_id: 'source', expected_previous_turn_id: 'source', config: { selected_candidate_id: 'b' },
  })
  expect(screen.queryByTestId('middleware-picker')).toBeNull()
  expect(screen.getByLabelText('消息输入')).toHaveValue('')
})

it('读取失败可恢复原指令与固定来源，刷新后的快照也能恢复', async () => {
  mockApi({ 'GET /api/v1/sessions/session/turns/source/artifact-candidates': {
    source_turn_id: 'source', candidates: [{ id: 'a', url: 'https://store/a.json', location: '/a' }],
  } })
  const turn = { id: 'failed', input: { role: 'user', content: '不添加对白' }, middleware_snapshot: snapshot } as Turn
  const send = vi.fn().mockResolvedValue(undefined)
  render(wrap(<MessageInput middlewareSessionId="session" restoreTurn={turn} onSend={send} />))
  await userEvent.click(screen.getByRole('button', { name: /恢复失败轮次/ }))
  expect(screen.getByLabelText('消息输入')).toHaveValue('不添加对白')
  expect(screen.getByLabelText('产物内容字段')).toHaveValue('/scenes')
  await waitFor(() => expect(screen.getByTestId('send-button')).toBeEnabled())
  await userEvent.click(screen.getByTestId('send-button'))
  const selection = send.mock.calls[0][1].middlewares[0]
  expect(selection.source_turn_id).toBe('source')
  expect(selection.expected_previous_turn_id).toBeUndefined()
})

it('保存的实际输入可折叠查看，失败错误可见', async () => {
  mockApi({ 'GET /api/v1/turns/failed': {
    id: 'failed', status: 'error', middleware_snapshot: snapshot, effective_input: null,
  } })
  render(wrap(<MiddlewareTurnDetails turnId="failed" sessionId="session" initial={snapshot} />))
  expect(screen.getByText('JSON 处理失败')).toBeInTheDocument()
  expect(screen.getByRole('alert')).toHaveTextContent('链接过期')
  expect(screen.getByText('查看处理配置与实际输入').closest('details')).not.toHaveAttribute('open')
})

it('大起始事件仍保留中间件标记，Trace 失败 Span 不显示成功', () => {
  const events = [
    mk('turn.start', { has_middleware: true, $blob: 'a'.repeat(64) }, { seq: 1 }),
    mk('span.start', { name: '读取 JSON', attrs: { category: 'middleware' } }, { seq: 2, step: 'mw' }),
    mk('span.end', { name: '读取 JSON', status: 'error', latency_ms: 12 }, { seq: 3, step: 'mw' }),
  ]
  expect(buildSessionState(events).turns[0].middlewareSnapshot).toBeDefined()
  expect(buildStepTree(events)[0].status).toBe('error')
})
