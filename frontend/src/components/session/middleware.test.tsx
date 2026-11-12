import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
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

it('聊天输入只发送文字，不显示本轮处理与产物入口', async () => {
  const send = vi.fn().mockResolvedValue(undefined)
  render(<MessageInput onSend={send} />)
  expect(screen.queryByText('本轮处理')).toBeNull()
  expect(screen.queryByText('本轮输出校验')).toBeNull()
  expect(screen.queryByText('附带上一轮产物')).toBeNull()
  await userEvent.type(screen.getByLabelText('消息输入'), '继续')
  await userEvent.click(screen.getByTestId('send-button'))
  expect(send).toHaveBeenCalledWith('继续', {})
})

it('历史失败输入可恢复文字，不复制历史中间件', async () => {
  const turn = { status: 'error', input: { role: 'user', content: '继续' }, middleware_snapshot: snapshot } as Turn
  const send = vi.fn().mockResolvedValue(undefined)
  render(<MessageInput onSend={send} restoreTurn={turn} />)
  await userEvent.click(screen.getByRole('button', { name: '恢复上次失败输入' }))
  await userEvent.click(screen.getByTestId('send-button'))
  expect(send).toHaveBeenCalledWith('继续', {})
})

it('保存的历史实际输入可折叠查看，失败错误可见', async () => {
  mockApi({ 'GET /api/v1/turns/failed': {
    id: 'failed', status: 'error', middleware_snapshot: snapshot, effective_input: null,
  } })
  render(wrap(<MiddlewareTurnDetails turnId="failed" sessionId="session" initial={snapshot} />))
  expect(screen.getByText('JSON 处理失败')).toBeInTheDocument()
  expect(screen.getByRole('alert')).toHaveTextContent('链接过期')
  expect(screen.getByText('查看处理配置与实际输入').closest('details')).not.toHaveAttribute('open')
})

it('大起始事件仍保留历史中间件标记，Trace 失败 Span 不显示成功', () => {
  const events = [
    mk('turn.start', { has_middleware: true, $blob: 'a'.repeat(64) }, { seq: 1 }),
    mk('span.start', { name: '读取 JSON', attrs: { category: 'middleware' } }, { seq: 2, step: 'mw' }),
    mk('span.end', { name: '读取 JSON', status: 'error', latency_ms: 12 }, { seq: 3, step: 'mw' }),
  ]
  expect(buildSessionState(events).turns[0].middlewareSnapshot).toBeDefined()
  expect(buildStepTree(events)[0].status).toBe('error')
})
