import type { Metadata } from 'next'
import '@fontsource-variable/geist'
import '@fontsource-variable/geist-mono'
import './globals.css'

export const metadata: Metadata = {
  title: 'Prism — See your product from every angle',
  description: 'Test your app, track competitors, research your industry — one platform for product teams',
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="font-sans">
        <header className="sticky top-0 z-30 border-b border-zinc-800/80 bg-zinc-950/80 backdrop-blur-sm px-6 py-4">
          <div className="max-w-6xl mx-auto flex items-center justify-between">
            <a href="/" className="flex items-center gap-2">
              <svg width="24" height="24" viewBox="0 0 100 100" fill="none">
                <path d="M50 10 L85 80 L15 80 Z" stroke="#10b981" strokeWidth="4" fill="none" />
                <path d="M50 10 L40 80" stroke="#10b981" strokeWidth="2" opacity="0.5" />
                <path d="M50 10 L60 80" stroke="#059669" strokeWidth="2" opacity="0.5" />
                <line x1="10" y1="45" x2="32" y2="45" stroke="#fafafa" strokeWidth="2" opacity="0.6" />
                <line x1="68" y1="50" x2="95" y2="38" stroke="#ef4444" strokeWidth="1.5" opacity="0.7" />
                <line x1="68" y1="52" x2="95" y2="47" stroke="#f97316" strokeWidth="1.5" opacity="0.7" />
                <line x1="68" y1="54" x2="95" y2="56" stroke="#22c55e" strokeWidth="1.5" opacity="0.7" />
                <line x1="68" y1="56" x2="95" y2="65" stroke="#06b6d4" strokeWidth="1.5" opacity="0.7" />
                <line x1="68" y1="58" x2="95" y2="74" stroke="#8b5cf6" strokeWidth="1.5" opacity="0.7" />
              </svg>
              <span className="text-lg font-semibold tracking-tight">Prism</span>
              <span className="text-[10px] text-zinc-600 bg-zinc-800 px-1.5 py-0.5 rounded">beta</span>
            </a>
            <span className="text-xs text-zinc-500">Product intelligence platform</span>
          </div>
        </header>
        <main className="max-w-6xl mx-auto px-6 py-8">{children}</main>
      </body>
    </html>
  )
}
