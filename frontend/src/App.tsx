import { useState } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { RouterProvider } from 'react-router-dom'
import { ApiError } from '@/api/client'
import { BootstrapGate } from '@/components/layout/BootstrapGate'
import { createAppRouter } from './routes'

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 10_000,
        refetchOnWindowFocus: false,
        // 4xx 是确定性错误，不重试；网络/5xx 最多重试 1 次
        retry: (count, err) => !(err instanceof ApiError && err.status >= 400 && err.status < 500) && count < 1,
      },
    },
  })
}

export default function App() {
  const [client] = useState(makeQueryClient)
  const [router] = useState(createAppRouter)
  return (
    <QueryClientProvider client={client}>
      <BootstrapGate>
        <RouterProvider router={router} future={{ v7_startTransition: true }} />
      </BootstrapGate>
    </QueryClientProvider>
  )
}
