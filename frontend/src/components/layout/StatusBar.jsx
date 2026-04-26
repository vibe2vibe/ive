import { useState, useRef, useEffect } from 'react'
import useStore from '../../state/store'
import MailboxPill from './MailboxPill'

const PEER_COLORS = ['#3b82f6', '#8b5cf6', '#ec4899', '#f59e0b', '#10b981', '#06b6d4', '#f97316', '#84cc16', '#ef4444', '#6366f1']

function IdentityEditor({ onClose }) {
  const myName = useStore((s) => s.myName)
  const myColor = useStore((s) => s.myColor)
  const setMyName = useStore((s) => s.setMyName)
  const setMyColor = useStore((s) => s.setMyColor)
  const [name, setName] = useState(myName || '')
  const inputRef = useRef(null)
  const panelRef = useRef(null)

  useEffect(() => { inputRef.current?.focus(); inputRef.current?.select() }, [])

  useEffect(() => {
    const h = (e) => {
      if (panelRef.current && !panelRef.current.contains(e.target)) onClose()
    }
    window.addEventListener('mousedown', h)
    return () => window.removeEventListener('mousedown', h)
  }, [onClose])

  const save = () => {
    const trimmed = name.trim()
    if (trimmed && trimmed !== myName) setMyName(trimmed)
    onClose()
  }

  return (
    <div
      ref={panelRef}
      className="absolute bottom-7 right-0 z-50 bg-bg-secondary border border-border-primary rounded-lg shadow-xl p-2.5 w-56"
    >
      <div className="text-[10px] text-text-faint uppercase tracking-wider mb-1.5">Your identity</div>
      <input
        ref={inputRef}
        value={name}
        onChange={(e) => setName(e.target.value)}
        onKeyDown={(e) => { if (e.key === 'Enter') save(); if (e.key === 'Escape') onClose() }}
        className="w-full bg-bg-primary border border-border-primary rounded px-2 py-1 text-xs text-text-primary outline-none focus:border-accent-primary mb-2"
        placeholder="Display name"
        maxLength={24}
      />
      <div className="flex gap-1 flex-wrap">
        {PEER_COLORS.map((c) => (
          <button
            key={c}
            className={`w-5 h-5 rounded-full transition-all ${myColor === c ? 'ring-2 ring-white/50 scale-110' : 'hover:scale-110'}`}
            style={{ background: c }}
            onClick={() => setMyColor(c)}
          />
        ))}
      </div>
    </div>
  )
}

export default function StatusBar() {
  const sessions = useStore((s) => s.sessions)
  const connected = useStore((s) => s.connected)
  const peers = useStore((s) => s.peers)
  const myName = useStore((s) => s.myName)
  const myColor = useStore((s) => s.myColor)
  const [showIdentity, setShowIdentity] = useState(false)

  const allSessions = Object.values(sessions)
  const runningCount = allSessions.filter((s) => s.status === 'running').length
  const totalCost = allSessions.reduce((sum, s) => sum + (Number(s.total_cost_usd) || 0), 0)
  const peerList = Object.entries(peers)
  const peerCount = peerList.length

  return (
    <>
      {!connected && (
        <div className="flex items-center justify-center gap-1.5 px-3 py-1.5 bg-red-500/8 border-t border-red-500/20 text-xs text-red-400">
          <span className="w-1.5 h-1.5 rounded-full bg-red-500 animate-subtle-pulse" />
          disconnected — reconnecting...
        </div>
      )}

      <div className="flex items-center h-6 px-2.5 bg-bg-inset border-t border-border-primary text-[11px] select-none">
        {/* Left section */}
        <div className="flex items-center gap-2.5">
          <div className={`flex items-center gap-1.5 px-1.5 py-0.5 -ml-1 rounded-sm ${connected ? 'text-text-faint' : 'text-red-400'}`}>
            <span className={`w-1.5 h-1.5 rounded-full ${connected ? 'bg-green-500' : 'bg-red-500'}`} />
            <span className="font-mono text-[10px]">{connected ? 'ok' : 'off'}</span>
          </div>

          {allSessions.length > 0 && (
            <span className="hidden sm:inline text-text-faint font-mono text-[10px]">{allSessions.length} sessions</span>
          )}

          {runningCount > 0 && (
            <span className="flex items-center gap-1 text-green-500 font-mono text-[10px]">
              <span className="w-1 h-1 rounded-full bg-green-500 animate-subtle-pulse" />
              {runningCount}<span className="hidden sm:inline">&nbsp;active</span>
            </span>
          )}

          {totalCost > 0 && (
            <span className="hidden sm:inline text-text-faint font-mono text-[10px]">${totalCost.toFixed(4)}</span>
          )}

          <MailboxPill position="above" />
        </div>

        <div className="flex-1" />

        {/* Right section — multiplayer presence + keyboard hints */}
        <div className="flex items-center gap-2.5">
          {/* Peer avatars */}
          {peerCount > 0 && (
            <div className="flex items-center gap-1" title={peerList.map(([, p]) => p.name).join(', ')}>
              <div className="flex -space-x-1">
                {peerList.slice(0, 4).map(([cid, p]) => (
                  <span
                    key={cid}
                    className="w-4 h-4 rounded-full text-[7px] font-bold text-white flex items-center justify-center ring-1 ring-bg-inset"
                    style={{ background: p.color }}
                    title={p.name}
                  >
                    {p.name?.[0]?.toUpperCase() || '?'}
                  </span>
                ))}
                {peerCount > 4 && (
                  <span className="w-4 h-4 rounded-full text-[7px] font-bold text-zinc-300 bg-zinc-700 flex items-center justify-center ring-1 ring-bg-inset">
                    +{peerCount - 4}
                  </span>
                )}
              </div>
              <span className="hidden sm:inline text-text-faint font-mono text-[10px]">{peerCount + 1} online</span>
            </div>
          )}

          {/* Your identity badge — click to edit */}
          <div className="relative">
            <button
              onClick={() => setShowIdentity(!showIdentity)}
              className="flex items-center gap-1 px-1.5 py-0.5 rounded hover:bg-bg-hover transition-colors"
            >
              <span
                className="w-3.5 h-3.5 rounded-full text-[7px] font-bold text-white flex items-center justify-center"
                style={{ background: myColor || '#6366f1' }}
              >
                {myName?.[0]?.toUpperCase() || '?'}
              </span>
              <span className="hidden sm:inline font-mono text-[10px] text-text-secondary max-w-[80px] truncate">{myName || 'Anonymous'}</span>
            </button>
            {showIdentity && <IdentityEditor onClose={() => setShowIdentity(false)} />}
          </div>

          <div className="hidden md:flex items-center gap-2 text-text-faint/60 font-mono text-[10px]">
            <span>⌘K</span>
            <span>⌘B</span>
            <span>⌘M</span>
            <span>⌘/</span>
            <span>⌘1-9</span>
            <span>⌘?</span>
          </div>
        </div>
      </div>
    </>
  )
}
