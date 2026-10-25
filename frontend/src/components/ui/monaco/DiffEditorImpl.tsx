import { useEffect, useRef } from 'react'
import { DiffEditor as MonacoDiffEditor, type DiffOnMount } from '@monaco-editor/react'
import './setup'
import { useThemeStore } from '@/stores/theme'
import type { DiffEditorProps } from '../DiffEditor'

export default function DiffEditorImpl({
  original,
  modified,
  language = 'yaml',
  height = 420,
  sideBySide = true,
  readOnly = true,
  onModifiedChange,
  reveal,
}: DiffEditorProps) {
  const theme = useThemeStore((s) => s.theme)
  const editorRef = useRef<Parameters<DiffOnMount>[0] | null>(null)

  const revealKey = reveal?.key
  useEffect(() => {
    if (!reveal || !editorRef.current) return
    const ed =
      reveal.side === 'original' ? editorRef.current.getOriginalEditor() : editorRef.current.getModifiedEditor()
    ed.revealLineInCenter(reveal.line)
    ed.setPosition({ lineNumber: reveal.line, column: 1 })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [revealKey])

  // 卸载时先解除 model 绑定，避免 "TextModel got disposed before DiffEditorWidget model got reset"
  useEffect(
    () => () => {
      try {
        editorRef.current?.setModel(null)
      } catch {
        /* 已被销毁 */
      }
      editorRef.current = null
    },
    [],
  )

  const onMount: DiffOnMount = (editor) => {
    editorRef.current = editor
    if (!onModifiedChange) return
    const mod = editor.getModifiedEditor()
    mod.onDidChangeModelContent(() => onModifiedChange(mod.getValue()))
  }

  return (
    <MonacoDiffEditor
      height={height}
      language={language}
      original={original}
      modified={modified}
      theme={theme === 'dark' ? 'vs-dark' : 'vs'}
      onMount={onMount}
      options={{
        readOnly,
        originalEditable: false,
        renderSideBySide: sideBySide,
        minimap: { enabled: false },
        fontSize: 13,
        fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
        scrollBeyondLastLine: false,
        automaticLayout: true,
        wordWrap: 'on',
        // 中文按字符 diff
        diffAlgorithm: 'advanced',
        ignoreTrimWhitespace: false,
        renderOverviewRuler: false,
      }}
    />
  )
}
