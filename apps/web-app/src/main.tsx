import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import './styles.css'

const root = document.getElementById('root')

if (!root) {
  throw new Error('找不到應用程式根節點')
}

createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
