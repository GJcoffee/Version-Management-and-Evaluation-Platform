/**
 * Monaco 本地打包配置。
 * 平台是离线可用的本地工具，不能从 CDN 加载 Monaco：
 *  1. `loader.config({ monaco })` 让 @monaco-editor/react 使用本地打包的实例；
 *  2. 通过 `?worker` 把 editor / json / ts worker 打进构建产物。
 * 此模块有副作用（设置 self.MonacoEnvironment），且体积较大，只应通过动态 import 加载
 * （CodeEditor / DiffEditor 已做 React.lazy）。
 */
import * as monaco from 'monaco-editor'
import { loader } from '@monaco-editor/react'
import EditorWorker from 'monaco-editor/editor/editor.worker?worker'
import JsonWorker from 'monaco-editor/language/json/json.worker?worker'
import TsWorker from 'monaco-editor/language/typescript/ts.worker?worker'

self.MonacoEnvironment = {
  getWorker(_moduleId: string, label: string): Worker {
    if (label === 'json') return new JsonWorker()
    if (label === 'typescript' || label === 'javascript') return new TsWorker()
    return new EditorWorker()
  },
}

loader.config({ monaco })

export { monaco }
