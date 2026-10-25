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
}: DiffEditorProps) {
  const theme = useThemeStore((s) => s.theme)

  const onMount: DiffOnMount = (editor) => {
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
