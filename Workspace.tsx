import { useCallback, useEffect, useRef, useState } from 'react'
import { SendHorizonal, Square } from 'lucide-react'
import { MessageBubble } from './MessageBubble'
import { ProcessingPane } from './ProcessingPane'
import type { ChatPhase, ChatSession, ProcessingSnapshot } from '../types'

type Props = {
  chat: ChatSession | null
  processing: ProcessingSnapshot
  phase: ChatPhase
  onSend: (value: string) => void
  onStop?: () => void
  initialPrompt?: string
}

const composerPlaceholder: Record<ChatPhase, string> = {
  intake:   'Answer the question or add more details...',
  running:  'Generating your plan — please wait...',
  complete: 'Ask to adjust budget, channels, or anything else...',
  error:    'Something went wrong. Try again or start a new campaign.',
}

// Below this width the grid collapses to a single column (see index.css),
// so the drag handle is hidden and the inline template must not be applied.
const SPLIT_BREAKPOINT = 1180
const SPLIT_KEY = 'markos.workspace.split'
const MIN_SPLIT = 0.25
const MAX_SPLIT = 0.75

function readStoredSplit(): number {
  if (typeof window === 'undefined') return 0.5
  const raw = window.localStorage.getItem(SPLIT_KEY)
  const parsed = raw ? Number(raw) : NaN
  if (!Number.isFinite(parsed)) return 0.5
  return Math.min(MAX_SPLIT, Math.max(MIN_SPLIT, parsed))
}

export function Workspace({ chat, processing, phase, onSend, onStop, initialPrompt }: Props) {
  const messages = chat?.messages ?? []
  const isRunning = phase === 'running'

  const gridRef = useRef<HTMLDivElement>(null)
  const [split, setSplit] = useState(readStoredSplit)
  const [dragging, setDragging] = useState(false)
  const [isWide, setIsWide] = useState(
    () => typeof window === 'undefined' || window.innerWidth > SPLIT_BREAKPOINT,
  )

  useEffect(() => {
    const query = window.matchMedia(`(min-width: ${SPLIT_BREAKPOINT + 1}px)`)
    const sync = () => setIsWide(query.matches)
    sync()
    query.addEventListener('change', sync)
    return () => query.removeEventListener('change', sync)
  }, [])

  const applyPointer = useCallback((clientX: number) => {
    const grid = gridRef.current
    if (!grid) return
    const rect = grid.getBoundingClientRect()
    if (rect.width === 0) return
    const ratio = (clientX - rect.left) / rect.width
    setSplit(Math.min(MAX_SPLIT, Math.max(MIN_SPLIT, ratio)))
  }, [])

  useEffect(() => {
    if (!dragging) return

    const onMove = (event: PointerEvent) => {
      event.preventDefault()
      applyPointer(event.clientX)
    }
    const onUp = () => setDragging(false)

    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
    window.addEventListener('pointercancel', onUp)
    // Stop the drag selecting text across both panes.
    const previousSelect = document.body.style.userSelect
    const previousCursor = document.body.style.cursor
    document.body.style.userSelect = 'none'
    document.body.style.cursor = 'col-resize'

    return () => {
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
      window.removeEventListener('pointercancel', onUp)
      document.body.style.userSelect = previousSelect
      document.body.style.cursor = previousCursor
    }
  }, [dragging, applyPointer])

  useEffect(() => {
    if (dragging) return
    window.localStorage.setItem(SPLIT_KEY, String(split))
  }, [dragging, split])

  const gridStyle = isWide
    ? { gridTemplateColumns: `minmax(0, ${split}fr) 10px minmax(0, ${1 - split}fr)` }
    : undefined

  return (
    <div
      ref={gridRef}
      className={`workspace-grid${dragging ? ' workspace-grid--dragging' : ''}`}
      style={gridStyle}
    >
      <section className="panel panel--chat">
        <div className="panel__header">
          <div>
            <span className="eyebrow">Conversation</span>
            <h2>{chat?.title ?? 'New campaign'}</h2>
          </div>
          <div className="status-pill">
            <span>{phase}</span>
          </div>
        </div>

        <div className="chat-stream">
          {initialPrompt && messages.length === 0 && (
            <article className="message message--user">
              <div className="message__meta">
                <strong>User</strong>
              </div>
              <p>{initialPrompt}</p>
            </article>
          )}
          {messages.length === 0 ? (
            <div className="empty-state">
              <p>Send a prompt to begin the campaign.</p>
            </div>
          ) : (
            messages.map((message) => <MessageBubble key={message.id} message={message} />)
          )}
        </div>

        <form
          className="composer composer--bottom"
          onSubmit={(event) => {
            event.preventDefault()
            if (isRunning) return
            const formData = new FormData(event.currentTarget)
            const value = String(formData.get('followup') ?? '').trim()
            if (!value) return
            onSend(value)
            event.currentTarget.reset()
          }}
        >
          <textarea
            name="followup"
            rows={2}
            placeholder={composerPlaceholder[phase]}
            disabled={isRunning}
            onKeyDown={(event) => {
              // Enter sends, Shift+Enter makes a new line.
              // isComposing guards IME input, where Enter confirms a candidate
              // and must not submit the message.
              if (event.key !== 'Enter' || event.shiftKey) return
              if (event.nativeEvent.isComposing) return
              event.preventDefault()
              if (isRunning) return
              event.currentTarget.form?.requestSubmit()
            }}
          />
          {isRunning && onStop ? (
            <button
              className="button button--stop"
              type="button"
              onClick={onStop}
              title="Stop generating"
            >
              <Square size={14} fill="currentColor" />
              <span>Stop</span>
            </button>
          ) : (
            <button
              className="button button--primary"
              type="submit"
              disabled={isRunning}
            >
              <SendHorizonal size={16} />
              <span>Send</span>
            </button>
          )}
        </form>
      </section>

      {isWide && (
        <div
          className="workspace-resizer"
          role="separator"
          aria-orientation="vertical"
          aria-label="Resize conversation and plan panes"
          aria-valuenow={Math.round(split * 100)}
          aria-valuemin={Math.round(MIN_SPLIT * 100)}
          aria-valuemax={Math.round(MAX_SPLIT * 100)}
          tabIndex={0}
          onPointerDown={(event) => {
            event.preventDefault()
            setDragging(true)
          }}
          onDoubleClick={() => setSplit(0.5)}
          onKeyDown={(event) => {
            if (event.key === 'ArrowLeft') {
              event.preventDefault()
              setSplit((current) => Math.max(MIN_SPLIT, current - 0.02))
            }
            if (event.key === 'ArrowRight') {
              event.preventDefault()
              setSplit((current) => Math.min(MAX_SPLIT, current + 0.02))
            }
            if (event.key === 'Enter') {
              event.preventDefault()
              setSplit(0.5)
            }
          }}
        >
          <span className="workspace-resizer__grip" />
        </div>
      )}

      <ProcessingPane processing={processing} />
    </div>
  )
}
