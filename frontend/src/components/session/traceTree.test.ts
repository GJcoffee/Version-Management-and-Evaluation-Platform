import { describe, expect, it } from 'vitest'
import {
  buildStepTree,
  computeTimeline,
  flattenTree,
  nodeMatches,
  timelineTicks,
  walkTree,
  type TraceNode,
} from './traceTree'
import { T0, mk, toolTurnEvents } from './testEvents'

const events = toolTurnEvents()

function ids(roots: readonly TraceNode[]): string[] {
  const out: string[] = []
  walkTree(roots, (n, d) => out.push(`${'-'.repeat(d)}${n.id}`))
  return out
}

describe('buildStepTree：成对与父子', () => {
  const roots = buildStepTree(events)

  it('llm.request/response、tool.call/result 各成对为一个节点，工具挂在发起它的 llm 下', () => {
    expect(roots).toHaveLength(3)
    expect(roots[0]).toMatchObject({ id: 's1', kind: 'llm', name: 'fake-model', status: 'ok', durationMs: 300 })
    expect(roots[0].events.map((e) => e.type)).toEqual(['llm.request', 'llm.response'])
    expect(roots[0].children).toHaveLength(1)
    expect(roots[0].children[0]).toMatchObject({
      id: 's2',
      kind: 'tool',
      name: 'query_order',
      parentId: 's1',
      durationMs: 50,
      mocked: true,
      status: 'ok',
    })
    expect(roots[1]).toMatchObject({ id: 's3', kind: 'llm' })
    expect(roots[2].kind).toBe('final')
  })

  it('时间：开始取 request/call 的时间戳，结束取 response/result', () => {
    expect(roots[0].startMs).toBe(T0 + 10)
    expect(roots[0].endMs).toBe(T0 + 310)
    const tool = roots[0].children[0]
    expect(tool.startMs).toBe(T0 + 315)
    expect(tool.endMs).toBe(T0 + 365)
  })

  it('span 成对；log 与 error 是瞬时节点；父节点缺失时挂到根', () => {
    const tree = buildStepTree([
      mk('span.start', { name: 'retrieve', attrs: { q: 1 } }, { seq: 1, step: 'a', at: 0 }),
      mk('log', { level: 'info', message: '命中 3 条' }, { seq: 2, step: null, parent: 'a', at: 5 }),
      mk('span.end', { name: 'retrieve', attrs: {}, latency_ms: 20 }, { seq: 3, step: 'a', at: 20 }),
      mk('error', { code: 'BOOM', message: 'x', retriable: false }, { seq: 4, parent: 'missing', at: 25 }),
    ])
    expect(ids(tree)).toEqual(['a', '-log#2', 'error#4'])
    expect(tree[0]).toMatchObject({ kind: 'span', name: 'retrieve', durationMs: 20, status: 'ok' })
    expect(tree[1]).toMatchObject({ kind: 'error', status: 'error', name: 'BOOM' })
  })

  it('进行中的步骤为 running，轮次结束后仍未结束的标为 incomplete', () => {
    const running = buildStepTree(events.slice(0, 4)) // 到 tool.call 为止
    expect(running[0].status).toBe('ok')
    expect(running[0].children[0]).toMatchObject({ status: 'running', endMs: null, durationMs: null })
    const ended = buildStepTree([...events.slice(0, 4), mk('turn.end', { status: 'cancelled' }, { seq: 99, at: 400 })])
    expect(ended[0].children[0].status).toBe('incomplete')
  })

  it('工具出错时节点为 error', () => {
    const tree = buildStepTree([
      mk('tool.call', { call_id: 'c', name: 't', arguments: {} }, { seq: 1, step: 's2' }),
      mk('tool.result', { call_id: 'c', name: 't', output: 'x', is_error: true, latency_ms: 4, mocked: false }, { seq: 2, step: 's2', at: 4 }),
    ])
    expect(tree[0].status).toBe('error')
  })

  it('环形引用不会死循环', () => {
    const tree = buildStepTree([
      mk('span.start', { name: 'a' }, { seq: 1, step: 'a', parent: 'b' }),
      mk('span.start', { name: 'b' }, { seq: 2, step: 'b', parent: 'a' }),
    ])
    expect(tree.length).toBeGreaterThan(0)
    expect(ids(tree)).toHaveLength(2)
  })
})

describe('flattenTree：折叠、筛选与搜索', () => {
  const roots = buildStepTree(events)

  it('默认全部展开；折叠后隐藏子节点', () => {
    expect(flattenTree(roots).map((r) => r.node.id)).toEqual(['s1', 's2', 's3', expect.any(String)])
    const rows = flattenTree(roots, { collapsed: new Set(['s1']) })
    expect(rows.map((r) => r.node.id)).toEqual(['s1', 's3', expect.any(String)])
    expect(rows[0]).toMatchObject({ hasChildren: true, expanded: false, depth: 0 })
  })

  it('按类型筛选：保留命中的节点及其祖先', () => {
    const rows = flattenTree(roots, { filter: { kinds: new Set(['tool']) } })
    expect(rows.map((r) => [r.node.id, r.depth])).toEqual([
      ['s1', 0],
      ['s2', 1],
    ])
  })

  it('文本搜索会匹配 payload 内容，并忽略折叠', () => {
    const rows = flattenTree(roots, { collapsed: new Set(['s1']), filter: { query: 'a001' } })
    expect(rows.map((r) => r.node.id)).toContain('s2')
    expect(flattenTree(roots, { filter: { query: '不存在的内容' } })).toHaveLength(0)
    const tool = roots[0].children[0]
    expect(nodeMatches(tool, { query: '已发货' })).toBe(true)
    expect(nodeMatches(tool, { query: 'query_order', kinds: new Set(['llm']) })).toBe(false)
  })
})

describe('computeTimeline：瀑布图时间计算', () => {
  const roots = buildStepTree(events)
  const flat: TraceNode[] = []
  walkTree(roots, (n) => flat.push(n))

  it('原点取最早开始时间，总时长到最晚结束', () => {
    const tl = computeTimeline(flat)
    expect(tl.originMs).toBe(T0 + 10)
    expect(tl.endMs).toBe(T0 + 571)
    expect(tl.totalMs).toBe(561)
  })

  it('每个节点的 left / width 是占总时长的比例', () => {
    const tl = computeTimeline(flat)
    const s1 = tl.bars.get('s1')!
    expect(s1.left).toBeCloseTo(0)
    expect(s1.width).toBeCloseTo(300 / 561)
    expect(s1.offsetMs).toBe(0)
    expect(s1.spanMs).toBe(300)
    const s2 = tl.bars.get('s2')!
    expect(s2.left).toBeCloseTo(305 / 561)
    expect(s2.width).toBeCloseTo(50 / 561)
    const s3 = tl.bars.get('s3')!
    expect(s3.left + s3.width).toBeCloseTo(1 - 1 / 561, 2)
  })

  it('可以指定轮次的起止时间（含 turn.start 到第一个步骤的空档）', () => {
    const tl = computeTimeline(flat, { originMs: T0, endMs: T0 + 575 })
    expect(tl.totalMs).toBe(575)
    expect(tl.bars.get('s1')!.left).toBeCloseTo(10 / 575)
  })

  it('运行中的节点画到 now；总时长随 now 延伸', () => {
    const running = buildStepTree(events.slice(0, 4))
    const list: TraceNode[] = []
    walkTree(running, (n) => list.push(n))
    const tl = computeTimeline(list, { nowMs: T0 + 1000 })
    const tool = tl.bars.get('s2')!
    expect(tool.open).toBe(true)
    expect(tool.spanMs).toBe(1000 - 315)
    expect(tl.endMs).toBe(T0 + 1000)
    expect(tool.left + tool.width).toBeCloseTo(1)
  })

  it('瞬时节点宽度为 0；空列表和单点不会除零', () => {
    const one = buildStepTree([mk('log', { level: 'info', message: 'x' }, { seq: 1, at: 0 })])
    const tl = computeTimeline(one)
    expect(tl.totalMs).toBe(1)
    expect(tl.bars.get('log#1')).toMatchObject({ left: 0, width: 0, spanMs: 0 })
    expect(computeTimeline([]).totalMs).toBe(1)
  })

  it('timelineTicks 给出整齐的刻度', () => {
    expect(timelineTicks(1000, 4)).toEqual([0, 250, 500, 750, 1000])
    expect(timelineTicks(561, 4)[0]).toBe(0)
    const t = timelineTicks(561, 4)
    expect(t[t.length - 1]).toBeLessThanOrEqual(561)
    expect(timelineTicks(0)).toEqual([0])
  })
})
