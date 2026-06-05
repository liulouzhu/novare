import { useEffect } from 'react'
import { AppLayout } from './components/layout/AppLayout'
import { useThemeStore } from './stores/themeStore'

export default function App() {
  // 初始化主题
  useEffect(() => {
    useThemeStore.getState().setTheme(useThemeStore.getState().theme)
  }, [])

  return <AppLayout />
}
