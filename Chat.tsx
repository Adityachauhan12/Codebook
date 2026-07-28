import { useEffect, useRef, useMemo } from 'react'
import { useLocation, useParams } from 'react-router-dom'
import { Workspace } from '../components/Workspace'
import { loadChat, upsertChat } from '../lib/storage'
import { useCampaignStream } from '../hooks/useCampaignStream'
import type { ChatSession } from '../types'

type LocationState = {
  prompt?: string
}

type Props = {
  onSessionLoaded: (session: ChatSession) => void
}

export function Chat({ onSessionLoaded }: Props) {
  const { chatId } = useParams()
  const location = useLocation()
  const state = (location.state ?? {}) as LocationState
  const hasSentRef = useRef(false)

  const session = useMemo(() => {
    if (!chatId) return null
    const saved = loadChat(chatId)
    if (saved) return saved
    if (!state.prompt) return null

    const timestamp = new Date().toISOString()
    const created: ChatSession = {
      id: chatId,
      title: state.prompt.slice(0, 48),
      createdAt: timestamp,
      updatedAt: timestamp,
      prompt: state.prompt,
      brief: null,
      messages: [],
      status: 'draft',
      summary: null,
      processing: null,
    }
    upsertChat(created)
    return created
  }, [chatId, state.prompt])

  useEffect(() => {
    if (session) onSessionLoaded(session)
  }, [onSessionLoaded, session])

  const { chat, processing, phase, send, stop } = useCampaignStream(session)

  // Auto-send the initial prompt through intake on first load
  useEffect(() => {
    if (!session || hasSentRef.current) return
    if (session.status === 'draft' && session.prompt.trim()) {
      hasSentRef.current = true
      void send(session.prompt)
    }
  }, [session?.id]) // eslint-disable-line react-hooks/exhaustive-deps

  if (!chat) {
    return (
      <section className="empty-route">
        <h2>Chat not found</h2>
        <p>Start a new campaign from the home screen.</p>
      </section>
    )
  }

  const initialPrompt = chat.messages.length === 0 && chat.status === 'draft' ? chat.prompt : undefined

  return (
    <Workspace
      chat={chat}
      processing={processing}
      phase={phase}
      onSend={send}
      onStop={stop}
      initialPrompt={initialPrompt}
    />
  )
}
