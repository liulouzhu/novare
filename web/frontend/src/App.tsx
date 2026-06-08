import { useState, useEffect } from 'react'
import { AppLayout } from './components/layout/AppLayout'
import { LoginPage } from './components/auth/LoginPage'
import { RegisterPage } from './components/auth/RegisterPage'
import { useThemeStore } from './stores/themeStore'
import { useAuthStore } from './stores/authStore'

type AuthPage = 'login' | 'register'

export default function App() {
  const theme = useThemeStore((s) => s.theme)
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated())
  const [authPage, setAuthPage] = useState<AuthPage>('login')

  // 初始化主题
  useEffect(() => {
    useThemeStore.getState().setTheme(useThemeStore.getState().theme)
  }, [])

  if (!isAuthenticated) {
    return authPage === 'login' ? (
      <LoginPage onSwitchToRegister={() => setAuthPage('register')} />
    ) : (
      <RegisterPage onSwitchToLogin={() => setAuthPage('login')} />
    )
  }

  return <AppLayout />
}
