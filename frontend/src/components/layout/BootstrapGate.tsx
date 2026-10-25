import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { bootstrap, ApiError } from '@/api/client'
import { Button } from '@/components/ui/Button'
import { Spinner } from '@/components/ui/Spinner'

type State = { kind: 'loading' } | { kind: 'ready' } | { kind: 'error'; message: string }

/** 应用启动时先请求 GET /auth/bootstrap（服务端下发 Cookie），成功后再渲染子树（路由） */
export function BootstrapGate({ children }: { children: ReactNode }) {
  const [state, setState] = useState<State>({ kind: 'loading' })

  const run = useCallback((signal?: AbortSignal) => {
    setState({ kind: 'loading' })
    bootstrap(signal).then(
      () => setState({ kind: 'ready' }),
      (e: unknown) => {
        if (signal?.aborted) return
        setState({ kind: 'error', message: e instanceof ApiError ? e.message : '初始化失败' })
      },
    )
  }, [])

  useEffect(() => {
    const ac = new AbortController()
    run(ac.signal)
    return () => ac.abort()
  }, [run])

  if (state.kind === 'ready') return <>{children}</>
  return (
    <div className="flex h-full flex-col items-center justify-center gap-3 bg-background text-foreground">
      {state.kind === 'loading' ? (
        <>
          <Spinner className="h-6 w-6" />
          <p className="text-sm text-muted-foreground">正在连接服务…</p>
        </>
      ) : (
        <>
          <p className="text-sm font-medium">无法连接到 EvalP 服务</p>
          <p className="max-w-md text-center text-xs text-muted-foreground">{state.message}</p>
          <Button onClick={() => run()}>重试</Button>
        </>
      )}
    </div>
  )
}
