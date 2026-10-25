import { Fragment } from 'react'
import { Link, useMatches, type Params, type UIMatch } from 'react-router-dom'
import { ChevronRight } from 'lucide-react'

/** 路由 handle：crumb 为固定文案，或根据路由参数生成 */
export interface RouteHandle {
  crumb?: string | ((params: Params<string>) => string)
}

export function Breadcrumbs() {
  const matches = useMatches() as UIMatch<unknown, RouteHandle | undefined>[]
  const crumbs = matches
    .filter((m) => m.handle?.crumb)
    .map((m) => {
      const c = m.handle!.crumb!
      return { path: m.pathname, label: typeof c === 'function' ? c(m.params) : c }
    })

  return (
    <nav aria-label="面包屑" className="flex min-w-0 items-center gap-1 text-sm">
      {crumbs.map((c, i) => {
        const last = i === crumbs.length - 1
        return (
          <Fragment key={c.path}>
            {i > 0 && <ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />}
            {last ? (
              <span className="truncate font-medium text-foreground" aria-current="page">
                {c.label}
              </span>
            ) : (
              <Link to={c.path} className="truncate text-muted-foreground hover:text-foreground">
                {c.label}
              </Link>
            )}
          </Fragment>
        )
      })}
    </nav>
  )
}
