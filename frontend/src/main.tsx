import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App'
import { recoverFromPreloadError } from './lib/preloadRecovery'
import { applyTheme, useThemeStore } from './stores/theme'

window.addEventListener('vite:preloadError', recoverFromPreloadError)
applyTheme(useThemeStore.getState().theme)

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
