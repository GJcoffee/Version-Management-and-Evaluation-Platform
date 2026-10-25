import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { ApiError, api } from '@/api/client'
import { draftKeys, saveDraft, validateDraft } from '@/api/queries/drafts'
import type { AgentSpec, Draft, ValidateResult } from '@/api/types'
import { setInYaml, parseSpecYaml, specToYaml, type ParsedYaml, type PathSeg } from '@/lib/specYaml'
import { toast } from '@/stores/toast'

export type SaveState = 'saved' | 'dirty' | 'saving' | 'error' | 'conflict' | 'invalid'

export interface ConflictInfo {
  server: Draft
}

/** 输入后多久做一次干跑校验（毫秒） */
export const VALIDATE_DEBOUNCE_MS = 500
/** 闲置多久后自动保存（毫秒）；每次输入都会重新计时 */
export const AUTOSAVE_IDLE_MS = 5000

/**
 * 草稿编辑器的状态：
 * - YAML 文本是唯一事实来源，表单修改通过 setInYaml 回写文本（保留注释）；
 * - 文本变化 500ms 后调用干跑校验（`POST /drafts/{id}/validate` 带 `{spec}`，不保存、不改 revision）；
 * - 保存是手动的（保存按钮 / Ctrl+S，即 `saveNow`），外加"闲置 5 秒后自动保存"；保存带 expected_revision，
 *   遇到 DRAFT_CONFLICT 进入冲突状态，由 ConflictDialog 处理；
 * - YAML 解析失败时不校验、不保存，也不丢失输入，表单被禁用。
 */
export function useDraftEditor(draft: Draft) {
  const qc = useQueryClient()
  const [text, setTextState] = useState(() => specToYaml(draft.spec))
  const [revision, setRevision] = useState(draft.revision)
  const [baseVersionId, setBaseVersionId] = useState(draft.base_version_id)
  const [savedSnap, setSavedSnap] = useState(() => JSON.stringify(draft.spec))
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)
  const [validation, setValidation] = useState<ValidateResult | null>(null)
  /** 校验结果对应的 Spec 快照（与当前内容一致时，结果才算"新鲜"） */
  const [validatedSnap, setValidatedSnap] = useState<string | null>(null)
  const [validating, setValidating] = useState(false)
  const [conflict, setConflict] = useState<ConflictInfo | null>(null)

  const parsed: ParsedYaml = useMemo(() => parseSpecYaml(text), [text])
  const lastValid = useRef<Record<string, unknown>>(draft.spec as unknown as Record<string, unknown>)
  if (parsed.ok) lastValid.current = parsed.value

  // refs：给异步流程读取最新值
  const parsedRef = useRef(parsed)
  parsedRef.current = parsed
  const revRef = useRef(draft.revision)
  const snapRef = useRef(savedSnap)
  const conflictRef = useRef<ConflictInfo | null>(null)
  const queue = useRef<Promise<unknown>>(Promise.resolve())
  const draftId = draft.id

  const dirty = parsed.ok ? JSON.stringify(parsed.value) !== savedSnap : true

  const validateSeq = useRef(0)
  /** 干跑校验（不保存）。多次调用时只采用最后一次的结果 */
  const runValidate = useCallback(
    async (spec: Record<string, unknown>) => {
      const seq = ++validateSeq.current
      const snap = JSON.stringify(spec)
      setValidating(true)
      try {
        const res = await validateDraft(draftId, spec as unknown as AgentSpec)
        if (seq !== validateSeq.current) return
        setValidation(res)
        setValidatedSnap(snap)
      } catch (e) {
        if (seq !== validateSeq.current) return
        // 结构错误也可能以 400 返回（VALIDATION_ERROR / SPEC_INVALID，details.errors）
        if (e instanceof ApiError && e.details && Array.isArray((e.details as { errors?: unknown }).errors)) {
          const d = e.details as { errors: ValidateResult['errors']; warnings?: ValidateResult['warnings'] }
          setValidation({ ok: false, errors: d.errors, warnings: d.warnings ?? [], resolved_preview: null })
          setValidatedSnap(snap)
        } else {
          toast.error('校验失败', { description: e instanceof Error ? e.message : undefined })
        }
      } finally {
        if (seq === validateSeq.current) setValidating(false)
      }
    },
    [draftId],
  )

  const handleConflict = useCallback(
    async (e: ApiError) => {
      try {
        const server = await api.get<Draft>(`/drafts/${draftId}`)
        const info = { server }
        conflictRef.current = info
        setConflict(info)
      } catch {
        toast.error('草稿冲突', { description: e.message })
      }
    },
    [draftId],
  )

  /** 保存（有改动时）。串行执行，返回是否成功（解析失败 / 冲突 / 出错为 false） */
  const doFlush = useCallback(async (): Promise<boolean> => {
    if (conflictRef.current) return false
    const p = parsedRef.current
    if (!p.ok) return false
    const snap = JSON.stringify(p.value)
    if (snap === snapRef.current) return true
    try {
      setSaving(true)
      setSaveError(null)
      const res = await saveDraft(draftId, p.value as unknown as AgentSpec, revRef.current)
      revRef.current = res.draft.revision
      snapRef.current = snap
      setRevision(res.draft.revision)
      setBaseVersionId(res.draft.base_version_id)
      setSavedSnap(snap)
      setSaving(false)
      qc.setQueryData(draftKeys.detail(draftId), res.draft)
      qc.invalidateQueries({ queryKey: draftKeys.all })
      return true
    } catch (e) {
      setSaving(false)
      if (e instanceof ApiError && e.code === 'DRAFT_CONFLICT') await handleConflict(e)
      else {
        const msg = e instanceof ApiError ? e.message : '保存失败'
        setSaveError(msg)
        toast.error('保存草稿失败', { description: msg })
      }
      return false
    }
  }, [draftId, qc, handleConflict])

  const flush = useCallback((): Promise<boolean> => {
    const next = queue.current.then(doFlush, doFlush) as Promise<boolean>
    queue.current = next
    return next
  }, [doFlush])

  // 文本变化 -> 防抖干跑校验（首次挂载立即校验一次）
  const firstRun = useRef(true)
  useEffect(() => {
    if (!parsed.ok) return
    const delay = firstRun.current ? 0 : VALIDATE_DEBOUNCE_MS
    firstRun.current = false
    const spec = parsed.value
    const t = setTimeout(() => void runValidate(spec), delay)
    return () => clearTimeout(t)
    // 只依赖 text：parsed 随 text 变化
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [text])

  // 文本变化 -> 闲置 5 秒后自动保存（每次输入都重新计时；手动保存会让 doFlush 发现无改动而直接返回）
  useEffect(() => {
    if (!parsed.ok || conflictRef.current) return
    if (JSON.stringify(parsed.value) === snapRef.current) return
    setSaveError(null)
    const t = setTimeout(() => void flush(), AUTOSAVE_IDLE_MS)
    return () => clearTimeout(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [text])

  const setText = useCallback((t: string) => setTextState(t), [])

  /** 表单修改：批量回写 YAML 文本 */
  const editSpec = useCallback((changes: Array<[PathSeg[], unknown]>) => {
    setTextState((cur) => changes.reduce((acc, [path, value]) => setInYaml(acc, path, value), cur))
  }, [])

  // 冲突处理
  const overwrite = useCallback(async () => {
    const c = conflictRef.current
    if (!c) return
    revRef.current = c.server.revision
    setRevision(c.server.revision)
    conflictRef.current = null
    setConflict(null)
    await flush()
  }, [flush])

  const discardLocal = useCallback(async () => {
    const c = conflictRef.current
    if (!c) return
    revRef.current = c.server.revision
    snapRef.current = JSON.stringify(c.server.spec)
    setRevision(c.server.revision)
    setBaseVersionId(c.server.base_version_id)
    setSavedSnap(snapRef.current)
    setTextState(specToYaml(c.server.spec))
    conflictRef.current = null
    setConflict(null)
    qc.setQueryData(draftKeys.detail(draftId), c.server)
    // 文本换成服务端内容后，上面的校验 effect 会自动重新校验
  }, [draftId, qc])

  /** 发布遇到冲突时由页面调用 */
  const reportConflict = useCallback((e: ApiError) => handleConflict(e), [handleConflict])

  const state: SaveState = conflict
    ? 'conflict'
    : !parsed.ok
      ? 'invalid'
      : saving
        ? 'saving'
        : saveError
          ? 'error'
          : dirty
            ? 'dirty'
            : 'saved'

  return {
    text,
    setText,
    editSpec,
    parsed,
    /** 表单展示用：解析失败时保留最后一次成功的结果 */
    formSpec: lastValid.current,
    revision,
    baseVersionId,
    state,
    dirty,
    validation,
    /** 校验结果是否对应当前编辑内容（输入后、防抖校验返回前为 false） */
    validationFresh: parsed.ok && validatedSnap === JSON.stringify(parsed.value),
    validating,
    conflict,
    saveNow: flush,
    overwrite,
    discardLocal,
    reportConflict,
    getRevision: () => revRef.current,
  }
}
