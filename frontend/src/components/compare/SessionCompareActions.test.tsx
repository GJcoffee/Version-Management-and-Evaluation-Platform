import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { Session } from '@/api/types'
import { mockApi } from '@/test/mockApi'
import { CompareSessionDialog, ReplayDialog } from './SessionCompareActions'

afterEach(() => vi.unstubAllGlobals())

const session = {
  id: 'ses_1',
  agent_id: 'agt_1',
  agent_name: 'bot',
  version_id: 'ver_1',
  version_seq: 1,
  mode: 'chat',
  title: '原会话',
  stats: { turns: 2 },
} as unknown as Session

const sess = (id: string, mode: string, title: string) => ({ ...session, id, mode, title })

function wrap(ui: React.ReactNode) {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>{ui}</MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('ReplayDialog：重放模式', () => {
  const routes = {
    'GET /api/v1/agents/agt_1/versions': {
      items: [
        { id: 'ver_1', seq: 1, tags: [] },
        { id: 'ver_2', seq: 2, tags: ['prod'] },
      ],
      next_cursor: null,
    },
    'GET /api/v1/sessions/ses_1': {
      session,
      turns: [
        { id: 'trn_0', idx: 0, status: 'ok', input: { role: 'user', content: '问题0' } },
        { id: 'trn_1', idx: 1, status: 'ok', input: { role: 'user', content: '问题1' } },
      ],
      events: null,
    },
    'POST /api/v1/sessions/ses_1/replay': { session_id: 'ses_2', compare_id: 'cmp_9' },
  }

  it('默认锚定模式；可切换到自由模式，请求带 mode', async () => {
    const api = mockApi(routes)
    wrap(<ReplayDialog session={session} onClose={() => {}} />)
    await screen.findByTestId('replay-turns')
    expect(screen.getByTestId('replay-mode-anchored')).toBeChecked()
    expect(screen.getByTestId('replay-mode')).toHaveTextContent('逐轮串行')
    await userEvent.click(screen.getByTestId('replay-mode-free'))
    expect(screen.getByTestId('replay-mode-free')).toBeChecked()
    await userEvent.click(screen.getByTestId('replay-submit'))
    await waitFor(() => expect(api.find('POST', /replay$/)).toHaveLength(1))
    expect(api.find('POST', /replay$/)[0].body).toEqual({ target_version_id: 'ver_2', mode: 'free' })
  })

  it('不改模式时发送 anchored（且全选轮次不带 turns）', async () => {
    const api = mockApi(routes)
    wrap(<ReplayDialog session={session} onClose={() => {}} />)
    await screen.findByTestId('replay-turns')
    await userEvent.click(screen.getByTestId('replay-submit'))
    await waitFor(() => expect(api.find('POST', /replay$/)).toHaveLength(1))
    expect(api.find('POST', /replay$/)[0].body).toEqual({ target_version_id: 'ver_2', mode: 'anchored' })
  })
})

describe('CompareSessionDialog：候选会话默认不含重放会话', () => {
  it('默认按 mode=chat 查询；勾选“包含重放会话”后带上 replay', async () => {
    const api = mockApi({
      'GET /api/v1/sessions': (c: { url: string }) => ({
        items: c.url.includes('replay')
          ? [sess('ses_a', 'chat', '正常会话'), sess('ses_r', 'replay', '重放：原会话')]
          : [sess('ses_a', 'chat', '正常会话')],
        next_cursor: null,
      }),
    })
    wrap(<CompareSessionDialog session={session} onClose={() => {}} />)
    const select = await screen.findByTestId('compare-other')
    expect(select).toHaveTextContent('正常会话')
    expect(select).not.toHaveTextContent('重放：原会话')
    expect(api.find('GET', /\/sessions\?/)[0].url).toContain('mode=chat')
    expect(api.find('GET', /\/sessions\?/)[0].url).not.toContain('replay')

    await userEvent.click(screen.getByTestId('include-replays'))
    await waitFor(() => expect(screen.getByTestId('compare-other')).toHaveTextContent('重放：原会话'))
    expect(api.find('GET', /\/sessions\?/).some((c) => decodeURIComponent(c.url).includes('mode=chat,replay'))).toBe(true)
  })
})
