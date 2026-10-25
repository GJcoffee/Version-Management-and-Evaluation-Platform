import { useEffect, useRef } from 'react'
import Editor, { type OnMount } from '@monaco-editor/react'
import { monaco } from './setup'
import { useThemeStore } from '@/stores/theme'
import type { CodeEditorProps } from '../CodeEditor'

const SEVERITY = {
  error: monaco.MarkerSeverity.Error,
  warning: monaco.MarkerSeverity.Warning,
  info: monaco.MarkerSeverity.Info,
} as const

export default function CodeEditorImpl({
  value,
  onChange,
  language = 'yaml',
  readOnly = false,
  height = 320,
  markers,
  wordWrap = true,
  lineNumbers = true,
  path,
  reveal,
}: CodeEditorProps) {
  const theme = useThemeStore((s) => s.theme)
  const editorRef = useRef<monaco.editor.IStandaloneCodeEditor | null>(null)

  const applyMarkers = () => {
    const model = editorRef.current?.getModel()
    if (!model) return
    monaco.editor.setModelMarkers(
      model,
      'evalp',
      (markers ?? []).map((m) => ({
        severity: SEVERITY[m.severity ?? 'error'],
        message: m.message,
        startLineNumber: m.line,
        startColumn: m.column ?? 1,
        endLineNumber: m.endLine ?? m.line,
        endColumn: m.endColumn ?? model.getLineMaxColumn(m.endLine ?? m.line),
      })),
    )
  }

  const onMount: OnMount = (editor) => {
    editorRef.current = editor
    applyMarkers()
  }

  useEffect(applyMarkers) // 每次渲染后同步标注（markers 通常很短）

  const revealKey = reveal?.key
  const revealLine = reveal?.line
  useEffect(() => {
    const ed = editorRef.current
    if (!ed || revealKey === undefined || !revealLine) return
    ed.revealLineInCenter(revealLine)
    ed.setPosition({ lineNumber: revealLine, column: 1 })
    ed.focus()
  }, [revealKey, revealLine])

  return (
    <Editor
      height={height}
      language={language}
      value={value}
      path={path}
      theme={theme === 'dark' ? 'vs-dark' : 'vs'}
      onChange={(v) => onChange?.(v ?? '')}
      onMount={onMount}
      options={{
        readOnly,
        minimap: { enabled: false },
        fontSize: 13,
        fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
        lineNumbers: lineNumbers ? 'on' : 'off',
        wordWrap: wordWrap ? 'on' : 'off',
        scrollBeyondLastLine: false,
        automaticLayout: true,
        tabSize: 2,
        renderLineHighlight: readOnly ? 'none' : 'line',
        scrollbar: { alwaysConsumeMouseWheel: false },
      }}
    />
  )
}
