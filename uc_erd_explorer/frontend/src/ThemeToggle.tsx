import { useEffect, useState } from 'react'
import type { CSSProperties } from 'react'

// Theme selection: "system" follows the OS (no data-theme attribute -> the CSS
// prefers-color-scheme block decides); "light"/"dark" force it via data-theme on <html>,
// which wins over the media query (see index.css). Persisted per-viewer in localStorage.
type Theme = 'system' | 'light' | 'dark'
const KEY = 'erd-theme'

function apply(theme: Theme) {
  const el = document.documentElement
  if (theme === 'system') el.removeAttribute('data-theme')
  else el.setAttribute('data-theme', theme)
}

function readStored(): Theme {
  try {
    const v = localStorage.getItem(KEY)
    if (v === 'light' || v === 'dark' || v === 'system') return v
  } catch {
    /* private mode / blocked storage -> fall back to system */
  }
  return 'system'
}

// Whether the app is currently rendering dark, resolving data-theme (explicit) over the
// OS preference (system). Re-renders on OS change and on data-theme mutations, so
// JS-driven colors (catalog palette, canvas dots) track the theme like the CSS tokens do.
export function useResolvedDark(): boolean {
  const compute = () => {
    const forced = document.documentElement.getAttribute('data-theme')
    if (forced === 'dark') return true
    if (forced === 'light') return false
    return window.matchMedia?.('(prefers-color-scheme: dark)').matches ?? false
  }
  const [dark, setDark] = useState(compute)
  useEffect(() => {
    const recompute = () => setDark(compute())
    const mq = window.matchMedia('(prefers-color-scheme: dark)')
    mq.addEventListener('change', recompute)
    const obs = new MutationObserver(recompute)
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] })
    return () => {
      mq.removeEventListener('change', recompute)
      obs.disconnect()
    }
  }, [])
  return dark
}

const NEXT: Record<Theme, Theme> = { system: 'light', light: 'dark', dark: 'system' }
const ICON: Record<Theme, string> = { system: '🖥', light: '☀', dark: '🌙' }
const LABEL: Record<Theme, string> = { system: 'System theme', light: 'Light theme', dark: 'Dark theme' }

export function ThemeToggle() {
  // Apply during the initializer (before first paint) so a dark viewer doesn't flash light.
  const [theme, setTheme] = useState<Theme>(() => {
    const t = readStored()
    apply(t)
    return t
  })

  useEffect(() => {
    apply(theme)
    try {
      localStorage.setItem(KEY, theme)
    } catch {
      /* ignore */
    }
  }, [theme])

  return (
    <button
      onClick={() => setTheme((t) => NEXT[t])}
      title={`${LABEL[theme]} — click to change`}
      aria-label={`${LABEL[theme]}. Click to change theme.`}
      style={styles.button}
    >
      <span aria-hidden>{ICON[theme]}</span>
    </button>
  )
}

const styles: Record<string, CSSProperties> = {
  button: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    width: 30,
    height: 30,
    borderRadius: 8,
    border: 'none',
    background: 'rgba(255,255,255,0.1)',
    color: 'var(--on-accent)',
    fontSize: 14,
    cursor: 'pointer',
    lineHeight: 1,
  },
}
