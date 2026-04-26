import { useState, useEffect, useRef, useCallback } from 'react'
import { Play, Square, RefreshCw, Loader2, X, GitBranch, Terminal as TerminalIcon } from 'lucide-react'
import useStore from '../../state/store'
import { demoApi, localPreviewUrl } from '../../lib/api'

// Per-workspace demo runner control panel. Renders status, branch/command
// inputs, action buttons, and a streaming log tail. Subscribes to global
// `demo_state` / `demo_log` ws events to live-update without polling.
export default function DemoPanel({ workspaceId, onClose, onPreviewUrl }) {
  const [demo, setDemo] = useState(null)
  const [branch, setBranch] = useState('main')
  const [command, setCommand] = useState('npm run dev')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const logRef = useRef(null)

  const globalWs = useStore((s) => s.ws)

  // ── Initial load + light polling fallback ─────────────────────────
  const refresh = useCallback(async () => {
    if (!workspaceId) return
    try {
      const d = await demoApi.status(workspaceId)
      setDemo(d)
      if (d?.branch) setBranch(d.branch)
      if (d?.command) setCommand(d.command)
    } catch (e) {
      setError(e.message)
    }
  }, [workspaceId])

  useEffect(() => {
    refresh()
    const t = setInterval(refresh, 5000)
    return () => clearInterval(t)
  }, [refresh])

  // ── Live ws updates ────────────────────────────────────────────────
  useEffect(() => {
    if (!globalWs) return
    function onMsg(e) {
      let data
      try { data = JSON.parse(e.data) } catch { return }
      if (data.type === 'demo_state' && data.demo?.workspace_id === workspaceId) {
        setDemo(data.demo)
      } else if (data.type === 'demo_log' && data.workspace_id === workspaceId) {
        setDemo((prev) => {
          if (!prev) return prev
          const merged = [...(prev.build_log_tail || []), ...(data.lines || [])]
          return { ...prev, build_log_tail: merged.slice(-200) }
        })
      }
    }
    globalWs.addEventListener('message', onMsg)
    return () => globalWs.removeEventListener('message', onMsg)
  }, [globalWs, workspaceId])

  // Auto-scroll log tail to bottom on update.
  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight
  }, [demo?.build_log_tail])

  // ── Actions ───────────────────────────────────────────────────────
  const guarded = useCallback(async (fn) => {
    setBusy(true)
    setError(null)
    try { await fn() } catch (e) { setError(e.message) } finally { setBusy(false) }
  }, [])

  const handleStart = () => guarded(async () => {
    const d = await demoApi.start(workspaceId, { branch, command })
    setDemo(d)
    if (d?.port && onPreviewUrl) onPreviewUrl(localPreviewUrl(d.port, '/'))
  })

  const handleStop = () => guarded(async () => {
    setDemo(await demoApi.stop(workspaceId))
  })

  const handlePull = () => guarded(async () => {
    setDemo(await demoApi.pullLatest(workspaceId))
  })

  const status = demo?.status || 'stopped'
  const port = demo?.port
  const sha = demo?.last_commit
  const lines = demo?.build_log_tail || []

  const pillColor = {
    running:  'bg-emerald-500/15 text-emerald-300 border-emerald-500/30',
    starting: 'bg-amber-500/15 text-amber-300 border-amber-500/30',
    building: 'bg-amber-500/15 text-amber-300 border-amber-500/30',
    error:    'bg-red-500/15 text-red-300 border-red-500/30',
    stopped:  'bg-zinc-800/60 text-zinc-400 border-zinc-700',
  }[status] || 'bg-zinc-800/60 text-zinc-400 border-zinc-700'

  const isRunning = status === 'running' || status === 'starting' || status === 'building'

  return (
    <div className="w-[360px] flex flex-col bg-[#0c0c12] border-l border-zinc-800 shrink-0">
      <div className="flex items-center gap-2 px-3 py-2 border-b border-zinc-800">
        <TerminalIcon size={12} className="text-emerald-400" />
        <span className="text-[11px] font-mono text-zinc-300 font-medium">Demo</span>
        <span className={`text-[9px] font-mono px-1.5 py-0.5 rounded border ${pillColor}`}>
          {status}
        </span>
        <div className="flex-1" />
        {onClose && (
          <button onClick={onClose} className="p-0.5 rounded hover:bg-zinc-800 text-zinc-600 hover:text-zinc-400">
            <X size={12} />
          </button>
        )}
      </div>

      <div className="px-3 py-2 space-y-2 border-b border-zinc-800">
        <div className="flex items-center gap-2">
          <GitBranch size={10} className="text-zinc-600 shrink-0" />
          <input
            type="text"
            value={branch}
            onChange={(e) => setBranch(e.target.value)}
            disabled={isRunning}
            placeholder="main"
            className="flex-1 px-2 py-1 text-[10px] font-mono bg-zinc-900 border border-zinc-700 rounded text-zinc-300 placeholder-zinc-600 focus:outline-none focus:border-indigo-500/50 disabled:opacity-50"
          />
          {sha && (
            <span className="text-[9px] font-mono text-zinc-500 shrink-0" title={sha}>
              @ {sha.slice(0, 7)}
            </span>
          )}
        </div>
        <input
          type="text"
          value={command}
          onChange={(e) => setCommand(e.target.value)}
          disabled={isRunning}
          placeholder="npm run dev"
          className="w-full px-2 py-1 text-[10px] font-mono bg-zinc-900 border border-zinc-700 rounded text-zinc-300 placeholder-zinc-600 focus:outline-none focus:border-indigo-500/50 disabled:opacity-50"
        />
        {port ? (
          <div className="text-[10px] font-mono text-zinc-500">
            port: <span className="text-zinc-300">{port}</span>
          </div>
        ) : null}
      </div>

      <div className="px-3 py-2 flex items-center gap-2 border-b border-zinc-800">
        {!isRunning ? (
          <button
            onClick={handleStart}
            disabled={busy}
            className="flex items-center gap-1.5 px-3 py-1.5 text-[10px] font-mono font-medium bg-emerald-600/20 hover:bg-emerald-600/30 text-emerald-300 border border-emerald-500/25 rounded transition-colors disabled:opacity-40"
          >
            {busy ? <Loader2 size={10} className="animate-spin" /> : <Play size={10} />}
            Start
          </button>
        ) : (
          <button
            onClick={handleStop}
            disabled={busy}
            className="flex items-center gap-1.5 px-3 py-1.5 text-[10px] font-mono font-medium bg-red-600/20 hover:bg-red-600/30 text-red-300 border border-red-500/25 rounded transition-colors disabled:opacity-40"
          >
            {busy ? <Loader2 size={10} className="animate-spin" /> : <Square size={10} />}
            Stop
          </button>
        )}
        <button
          onClick={handlePull}
          disabled={busy || status === 'stopped'}
          className="flex items-center gap-1.5 px-3 py-1.5 text-[10px] font-mono font-medium bg-indigo-600/20 hover:bg-indigo-600/30 text-indigo-300 border border-indigo-500/25 rounded transition-colors disabled:opacity-40"
          title="git pull origin <branch> + reinstall (if needed) + restart on same port"
        >
          {busy && status === 'building' ? <Loader2 size={10} className="animate-spin" /> : <RefreshCw size={10} />}
          Pull Latest
        </button>
      </div>

      {error && (
        <div className="px-3 py-1.5 text-[10px] font-mono text-red-400 bg-red-500/10 border-b border-red-500/20">
          {error}
        </div>
      )}
      {demo?.error && status === 'error' && (
        <div className="px-3 py-1.5 text-[10px] font-mono text-red-400 bg-red-500/10 border-b border-red-500/20 break-all">
          {demo.error}
        </div>
      )}

      <div
        ref={logRef}
        className="flex-1 overflow-y-auto px-3 py-2 bg-[#06060a] font-mono text-[10px] text-zinc-400 leading-relaxed"
      >
        {lines.length === 0 ? (
          <span className="text-zinc-700">no log output yet…</span>
        ) : (
          lines.slice(-50).map((l, i) => (
            <div key={i} className="whitespace-pre-wrap break-all">{l}</div>
          ))
        )}
      </div>
    </div>
  )
}
