import { useState, useEffect, useCallback } from 'react'
import {
  Ticket,
  X,
  Plus,
  Copy,
  Check,
  Trash2,
  Loader2,
  AlertCircle,
  QrCode,
  Eye,
  EyeOff,
  Smartphone,
} from 'lucide-react'
import { QRCodeSVG } from 'qrcode.react'
import { api, ApiError } from '../../lib/api'

const MODE_OPTIONS = [
  { id: 'brief', label: 'Brief', hint: 'Create tasks, comment, advise. No execution.' },
  { id: 'code',  label: 'Code',  hint: 'Drive sessions in auto/plan. No Bash unless allowlisted.' },
  { id: 'full',  label: 'Full',  hint: 'Owner-equivalent. TTL-bounded.' },
]

const TTL_OPTIONS = [
  { value: 0,        label: 'Session-only' },
  { value: 3600,     label: '1 hour' },
  { value: 28800,    label: '8 hours' },
  { value: 2592000,  label: '30 days' },
]

const BRIEF_SUBSCOPES = [
  { value: 'read_only',      label: 'Read-only' },
  { value: 'create_comment', label: 'Comment + create' },
]

function statusOf(invite) {
  if (invite.burned_at)   return { tone: 'red',     label: 'Revoked' }
  if (invite.redeemed_at) return { tone: 'zinc',    label: 'Redeemed' }
  if (invite.expires_at && new Date(invite.expires_at + 'Z') < new Date())
    return { tone: 'amber', label: 'Expired' }
  return { tone: 'emerald', label: 'Active' }
}

function CopyButton({ value, label }) {
  const [copied, setCopied] = useState(false)
  const handle = async () => {
    try {
      await navigator.clipboard.writeText(value)
      setCopied(true)
      setTimeout(() => setCopied(false), 1400)
    } catch {
      // clipboard might be blocked on insecure origins; fall back silently
    }
  }
  return (
    <button
      onClick={handle}
      title={`Copy ${label}`}
      className="flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded border border-border-secondary text-text-faint hover:text-text-secondary hover:bg-bg-hover transition-colors"
    >
      {copied ? <Check size={10} /> : <Copy size={10} />}
      {copied ? 'Copied' : label}
    </button>
  )
}

function CreateInviteForm({ onCreated, onCancel }) {
  const [mode, setMode] = useState('code')
  const [briefSubscope, setBriefSubscope] = useState('create_comment')
  const [ttlSeconds, setTtlSeconds] = useState(28800)
  const [label, setLabel] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState(null)

  const handle = async (e) => {
    e.preventDefault()
    setSubmitting(true)
    setError(null)
    try {
      const body = {
        mode,
        ttl_seconds: ttlSeconds,
        label: label.trim() || null,
      }
      if (mode === 'brief') body.brief_subscope = briefSubscope
      const created = await api.createInvite(body)
      onCreated(created)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <form
      onSubmit={handle}
      className="p-4 bg-bg-secondary border border-border-secondary rounded-lg space-y-3"
    >
      <div className="flex items-center gap-2">
        <Plus size={14} className="text-emerald-400" />
        <span className="text-xs text-text-primary font-medium">New invite</span>
        <div className="flex-1" />
        <button
          type="button"
          onClick={onCancel}
          className="p-1 rounded-md hover:bg-bg-hover text-text-faint hover:text-text-secondary transition-colors"
        >
          <X size={13} />
        </button>
      </div>

      {/* Mode */}
      <div>
        <label className="text-[10px] text-text-faint uppercase tracking-wide">Mode</label>
        <div className="flex gap-1.5 mt-1">
          {MODE_OPTIONS.map((m) => (
            <button
              key={m.id}
              type="button"
              onClick={() => setMode(m.id)}
              className={`flex-1 px-2.5 py-1.5 text-[11px] rounded-md border transition-colors ${
                mode === m.id
                  ? 'bg-accent-subtle border-accent-primary text-indigo-300 font-medium'
                  : 'border-border-secondary text-text-faint hover:text-text-secondary hover:bg-bg-hover'
              }`}
            >
              {m.label}
            </button>
          ))}
        </div>
        <p className="text-[10px] text-text-faint mt-1.5">
          {MODE_OPTIONS.find((m) => m.id === mode)?.hint}
        </p>
      </div>

      {mode === 'brief' && (
        <div>
          <label className="text-[10px] text-text-faint uppercase tracking-wide">Brief sub-scope</label>
          <div className="flex gap-1.5 mt-1">
            {BRIEF_SUBSCOPES.map((s) => (
              <button
                key={s.value}
                type="button"
                onClick={() => setBriefSubscope(s.value)}
                className={`flex-1 px-2.5 py-1.5 text-[11px] rounded-md border transition-colors ${
                  briefSubscope === s.value
                    ? 'bg-accent-subtle border-accent-primary text-indigo-300 font-medium'
                    : 'border-border-secondary text-text-faint hover:text-text-secondary hover:bg-bg-hover'
                }`}
              >
                {s.label}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* TTL */}
      <div>
        <label className="text-[10px] text-text-faint uppercase tracking-wide">Joiner session TTL</label>
        <div className="flex gap-1.5 mt-1 flex-wrap">
          {TTL_OPTIONS.map((t) => (
            <button
              key={t.value}
              type="button"
              onClick={() => setTtlSeconds(t.value)}
              className={`px-2.5 py-1.5 text-[11px] rounded-md border transition-colors ${
                ttlSeconds === t.value
                  ? 'bg-accent-subtle border-accent-primary text-indigo-300 font-medium'
                  : 'border-border-secondary text-text-faint hover:text-text-secondary hover:bg-bg-hover'
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>
        <p className="text-[10px] text-text-faint mt-1.5">
          The invite link itself expires in 24h. The redeemed session lives this long after redemption.
        </p>
      </div>

      {/* Label */}
      <div>
        <label className="text-[10px] text-text-faint uppercase tracking-wide">Label (optional)</label>
        <input
          value={label}
          onChange={(e) => setLabel(e.target.value)}
          placeholder="e.g. Sara's iPhone"
          className="w-full mt-1 bg-bg-primary border border-border-secondary rounded-md px-2.5 py-1.5 text-xs text-text-primary placeholder:text-text-faint outline-none focus:border-accent-primary"
        />
      </div>

      {error && (
        <div className="flex items-start gap-1.5 text-[11px] text-red-300 bg-red-500/10 border border-red-500/20 rounded-md p-2">
          <AlertCircle size={12} className="mt-0.5 shrink-0" />
          <span className="font-mono">{error}</span>
        </div>
      )}

      <button
        type="submit"
        disabled={submitting}
        className="w-full flex items-center justify-center gap-1.5 bg-accent-primary text-white text-xs font-medium py-2 rounded-md hover:opacity-90 disabled:opacity-50 transition-opacity"
      >
        {submitting && <Loader2 size={12} className="animate-spin" />}
        {submitting ? 'Generating…' : 'Generate invite'}
      </button>
    </form>
  )
}

function NewInviteResult({ invite, onDone }) {
  const [showUrl, setShowUrl] = useState(false)
  const origin = typeof window !== 'undefined' ? window.location.origin : ''
  const host = typeof window !== 'undefined' ? window.location.host : ''
  const joinPlain = `${origin}/join`
  const joinMagic = `${origin}/join?t=${encodeURIComponent(invite.secret_qr)}`
  const isLocalhost = /^(localhost|127\.|0\.0\.0\.0|\[::1\])/.test(host)

  return (
    <div className="p-4 bg-emerald-500/5 border border-emerald-500/30 rounded-lg space-y-4">
      <div className="flex items-center gap-2">
        <Check size={14} className="text-emerald-400" />
        <span className="text-xs text-text-primary font-medium">Invite generated</span>
        <div className="flex-1" />
        <button
          onClick={onDone}
          className="text-[10px] text-text-faint hover:text-text-secondary"
        >
          Close
        </button>
      </div>
      <p className="text-[10px] text-amber-300/90 leading-relaxed">
        These projections are shown <span className="font-medium">once</span>. Save what you'll send;
        anything you don't copy now is gone (revoke + recreate to rotate).
      </p>

      {/* How-to-share callout */}
      <div className="p-3 bg-bg-primary border border-border-secondary rounded-md space-y-1.5">
        <div className="flex items-center gap-1.5 text-[11px] text-text-primary font-medium">
          <Smartphone size={12} className="text-cyan-400" />
          How they join
        </div>
        <p className="text-[11px] text-text-secondary leading-relaxed">
          On their phone or laptop, have them open{' '}
          <span className="font-mono text-cyan-300">{host}/join</span>{' '}
          and paste any of the projections below. Or scan the magic-link QR to skip the paste.
        </p>
        {isLocalhost && (
          <p className="text-[10px] text-amber-300/90 flex items-start gap-1 mt-1">
            <AlertCircle size={10} className="mt-0.5 shrink-0" />
            You're serving on <span className="font-mono">{host}</span>. Phones on other networks
            can't reach this. Restart with <span className="font-mono">--tunnel</span> or use your
            machine's LAN IP for them to actually connect.
          </p>
        )}
      </div>

      {/* Speakable */}
      <div>
        <div className="flex items-center justify-between mb-1">
          <span className="text-[10px] text-text-faint uppercase tracking-wide">Speakable (4 words)</span>
          <CopyButton value={invite.secret_speakable} label="Copy" />
        </div>
        <div className="font-mono text-sm text-emerald-300 bg-bg-primary border border-border-secondary rounded-md px-3 py-2 break-words">
          {invite.secret_speakable}
        </div>
        <p className="text-[10px] text-text-faint mt-1">
          Easiest to read aloud over a call. Hyphens, spaces, case — all forgiven by the decoder.
        </p>
      </div>

      {/* Compact */}
      <div>
        <div className="flex items-center justify-between mb-1">
          <span className="text-[10px] text-text-faint uppercase tracking-wide">Compact (12 chars)</span>
          <CopyButton value={invite.secret_compact} label="Copy" />
        </div>
        <div className="font-mono text-sm text-cyan-300 bg-bg-primary border border-border-secondary rounded-md px-3 py-2 tracking-wider">
          {invite.secret_compact}
        </div>
        <p className="text-[10px] text-text-faint mt-1">
          Crockford base32 — no I, L, O, or U. 1↔l and 0↔O are auto-corrected.
        </p>
      </div>

      {/* QR / magic link */}
      <div>
        <div className="flex items-center justify-between mb-1">
          <span className="text-[10px] text-text-faint uppercase tracking-wide">Magic link (scan to auto-redeem)</span>
          <div className="flex items-center gap-1">
            <button
              onClick={() => setShowUrl((v) => !v)}
              className="flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded border border-border-secondary text-text-faint hover:text-text-secondary hover:bg-bg-hover transition-colors"
            >
              {showUrl ? <EyeOff size={10} /> : <Eye size={10} />}
              {showUrl ? 'Hide' : 'Show'} URL
            </button>
            <CopyButton value={joinMagic} label="Copy URL" />
          </div>
        </div>
        <div className="flex items-start gap-3 bg-bg-primary border border-border-secondary rounded-md p-3">
          <div className="bg-white p-2 rounded shrink-0">
            <QRCodeSVG value={joinMagic} size={132} level="M" includeMargin={false} />
          </div>
          <div className="flex-1 text-[10px] text-text-faint leading-relaxed">
            <p className="text-text-secondary mb-1">Scan in person.</p>
            <p>
              Don't paste this URL into chat apps or email — preview bots will follow it and burn
              the token before the recipient sees it. To send remotely, share the words or compact
              code above and let them paste at <span className="font-mono">{host}/join</span>.
            </p>
          </div>
        </div>
        {showUrl && (
          <div className="font-mono text-[11px] text-purple-300 bg-bg-primary border border-border-secondary rounded-md px-3 py-2 break-all mt-2">
            {joinMagic}
          </div>
        )}
      </div>

      {/* Bare /join QR — safe to share over any channel */}
      <div>
        <div className="flex items-center justify-between mb-1">
          <span className="text-[10px] text-text-faint uppercase tracking-wide">Join page (no token — safe to share)</span>
          <CopyButton value={joinPlain} label="Copy URL" />
        </div>
        <div className="flex items-start gap-3 bg-bg-primary border border-border-secondary rounded-md p-3">
          <div className="bg-white p-2 rounded shrink-0">
            <QRCodeSVG value={joinPlain} size={96} level="M" includeMargin={false} />
          </div>
          <div className="flex-1 text-[10px] text-text-faint leading-relaxed">
            <p className="text-text-secondary mb-1">
              <span className="font-mono">{host}/join</span>
            </p>
            <p>
              This QR has no token in it — paste-only landing page. Send it however you like, then
              tell them the words/compact code separately.
            </p>
          </div>
        </div>
      </div>
    </div>
  )
}

export default function InvitesPanel({ onClose }) {
  const [invites, setInvites] = useState([])
  const [loading, setLoading] = useState(true)
  const [creating, setCreating] = useState(false)
  const [justCreated, setJustCreated] = useState(null)
  const [revoking, setRevoking] = useState(null)
  const [error, setError] = useState(null)

  const reload = useCallback(async () => {
    try {
      const r = await api.listInvites()
      setInvites(Array.isArray(r?.invites) ? r.invites : [])
      setError(null)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    reload()
  }, [reload])

  const handleCreated = async (created) => {
    setJustCreated(created)
    setCreating(false)
    await reload()
  }

  const handleRevoke = async (id) => {
    setRevoking(id)
    try {
      await api.revokeInvite(id)
      await reload()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err))
    } finally {
      setRevoking(null)
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center pt-[8vh] bg-black/50"
      onClick={onClose}
    >
      <div
        className="w-[640px] ide-panel overflow-hidden scale-in max-h-[80vh] overflow-y-auto"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center gap-2 px-4 py-2.5 border-b border-border-primary sticky top-0 bg-bg-primary z-10">
          <Ticket size={14} className="text-cyan-400" />
          <span className="text-xs text-text-primary font-medium">Invites</span>
          <span className="text-[10px] text-cyan-400/70 px-1.5 py-0.5 bg-cyan-500/8 rounded border border-cyan-500/15">
            One-shot
          </span>
          <span className="text-[10px] text-text-faint font-mono ml-1">
            recipient pastes at /join — share words, compact code, or scan QR
          </span>
          <div className="flex-1" />
          <button
            onClick={onClose}
            className="p-1 rounded-md hover:bg-bg-hover text-text-faint hover:text-text-secondary transition-colors"
          >
            <X size={15} />
          </button>
        </div>

        <div className="p-4 space-y-3">
          {error && (
            <div className="flex items-start gap-1.5 text-[11px] text-red-300 bg-red-500/10 border border-red-500/20 rounded-md p-2">
              <AlertCircle size={12} className="mt-0.5 shrink-0" />
              <span className="font-mono">{error}</span>
            </div>
          )}

          {justCreated && (
            <NewInviteResult invite={justCreated} onDone={() => setJustCreated(null)} />
          )}

          {creating ? (
            <CreateInviteForm onCreated={handleCreated} onCancel={() => setCreating(false)} />
          ) : (
            !justCreated && (
              <button
                onClick={() => setCreating(true)}
                className="w-full flex items-center justify-center gap-1.5 bg-bg-secondary border border-dashed border-border-secondary rounded-lg py-2 text-xs text-text-secondary hover:bg-bg-hover hover:border-border-primary transition-colors"
              >
                <Plus size={13} />
                New invite
              </button>
            )
          )}

          {/* List */}
          <div className="space-y-2">
            {loading ? (
              <div className="flex items-center justify-center gap-2 py-8 text-xs text-text-faint">
                <Loader2 size={14} className="animate-spin" />
                Loading…
              </div>
            ) : invites.length === 0 ? (
              <div className="text-center py-8 text-xs text-text-faint">
                No invites yet. Generate one to share access.
              </div>
            ) : (
              invites.map((inv) => {
                const status = statusOf(inv)
                const toneClass = {
                  emerald: 'text-emerald-300 bg-emerald-500/10 border-emerald-500/20',
                  red:     'text-red-300 bg-red-500/10 border-red-500/20',
                  zinc:    'text-zinc-300 bg-zinc-700/30 border-zinc-600/30',
                  amber:   'text-amber-300 bg-amber-500/10 border-amber-500/20',
                }[status.tone]
                const isActive = status.label === 'Active'
                return (
                  <div
                    key={inv.id}
                    className="p-3 bg-bg-secondary border border-border-secondary rounded-lg"
                  >
                    <div className="flex items-center gap-2 mb-1.5">
                      <span className="text-xs text-text-primary font-medium">
                        {inv.label || <span className="text-text-faint italic">unlabeled</span>}
                      </span>
                      <span className={`text-[9px] font-mono px-1.5 py-0.5 rounded border ${toneClass}`}>
                        {status.label}
                      </span>
                      <span className="text-[9px] font-mono px-1.5 py-0.5 rounded text-purple-300 bg-purple-500/10 border border-purple-500/20 uppercase">
                        {inv.mode}
                        {inv.brief_subscope ? ` · ${inv.brief_subscope}` : ''}
                      </span>
                      <div className="flex-1" />
                      {isActive && (
                        <button
                          onClick={() => handleRevoke(inv.id)}
                          disabled={revoking === inv.id}
                          className="flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded border border-red-500/30 text-red-300 hover:bg-red-500/10 transition-colors disabled:opacity-50"
                        >
                          {revoking === inv.id
                            ? <Loader2 size={10} className="animate-spin" />
                            : <Trash2 size={10} />}
                          Revoke
                        </button>
                      )}
                    </div>

                    <div className="flex items-center gap-2 text-[10px] text-text-faint font-mono">
                      <span>TTL {inv.ttl_seconds === 0 ? 'session-only' : inv.ttl_seconds + 's'}</span>
                      <span>·</span>
                      <span>Expires {inv.expires_at}</span>
                      {inv.redemption_attempts > 0 && (
                        <>
                          <span>·</span>
                          <span className="text-amber-300">{inv.redemption_attempts} bad attempt{inv.redemption_attempts === 1 ? '' : 's'}</span>
                        </>
                      )}
                    </div>

                    <div className="flex items-center gap-3 mt-2 text-[10px] text-text-faint">
                      <span className="font-mono text-emerald-300/80">{inv.encoded_speakable}</span>
                      <span className="font-mono text-cyan-300/80 tracking-wider">{inv.encoded_compact}</span>
                    </div>
                  </div>
                )
              })
            )}
          </div>

          <p className="text-[10px] text-text-faint italic flex items-center gap-1 pt-1">
            <QrCode size={10} />
            Speakable + compact projections are stored for display only — the secret itself was shown once at creation.
          </p>
        </div>
      </div>
    </div>
  )
}
