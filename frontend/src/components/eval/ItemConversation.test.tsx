import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import userEvent from '@testing-library/user-event'
import { expect, it, vi } from 'vitest'
import { buildSessionState } from '@/components/session/sessionState'
import { toolTurnEvents } from '@/components/session/testEvents'
import { ItemConversation } from './ItemDetail'

vi.mock('@/components/session/useSessionStream', () => ({
  useSessionStream: () => ({
    turns: buildSessionState(toolTurnEvents('turn-1')).turns,
    loading: false,
  }),
}))

it('评测会话默认折叠，切换会话后重置为折叠', async () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const conversation = (sessionId: string) => (
    <QueryClientProvider client={client}><ItemConversation sessionId={sessionId} /></QueryClientProvider>
  )
  const { rerender } = render(conversation('ses_a'))
  expect(screen.getByTestId('turn')).toHaveAttribute('data-collapsed', 'true')
  expect(screen.queryByTestId('user-message')).toBeNull()
  await userEvent.click(screen.getByTestId('turn-header'))
  expect(screen.getByTestId('user-message')).toBeInTheDocument()
  rerender(conversation('ses_b'))
  expect(screen.getByTestId('turn')).toHaveAttribute('data-collapsed', 'true')
  expect(screen.queryByTestId('user-message')).toBeNull()
})
