import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { loadCampaigns, mergeCampaigns } from '../lib/storage'
import { fetchCampaigns } from '../lib/api'
import { formatCurrency, formatDate } from '../lib/utils'
import type { CampaignRecord } from '../types'

function getAlignmentTone(status: string | undefined) {
  if (status === 'aligned') return 'aligned'
  if (status === 'warnings') return 'warnings'
  return 'pending'
}

export function Campaigns() {
  // Seed from localStorage so the list paints instantly, then reconcile with
  // the server -- which is what makes the same campaigns visible on every machine.
  const [campaigns, setCampaigns] = useState<CampaignRecord[]>(() => loadCampaigns())
  const [syncError, setSyncError] = useState<string | null>(null)

  useEffect(() => {
    const controller = new AbortController()

    fetchCampaigns(controller.signal)
      .then((remote) => {
        setSyncError(null)
        setCampaigns((local) => mergeCampaigns(remote, local))
      })
      .catch((error: unknown) => {
        if (error instanceof Error && error.name === 'AbortError') return
        setSyncError('Showing campaigns saved on this device — the server list is unavailable.')
      })

    return () => controller.abort()
  }, [])

  return (
    <section className="page-stack">
      <header className="page-header">
        <div>
          <span className="eyebrow">Campaigns</span>
          <h1>Generated plans</h1>
          <p>{campaigns.length} campaign plans ready to review.</p>
          {syncError && <p className="page-header__note">{syncError}</p>}
        </div>
      </header>

      <section className="home-campaign-preview">
        <div className="home-campaign-preview__grid">
        {campaigns.length === 0 ? (
          <div className="empty-state empty-state--wide">
            <p>No saved campaigns yet. Complete a chat run to generate one.</p>
          </div>
        ) : (
          campaigns.map((campaign) => (
            <Link key={campaign.id} to={`/campaigns/${campaign.id}`} className="campaign-card campaign-card--clickable">
              <div className="campaign-card__top">
                <div>
                  <span className={`campaign-card__badge campaign-card__badge--${getAlignmentTone(campaign.summary.alignment_status)}`}>
                    {campaign.summary.alignment_status ?? 'pending'}
                  </span>
                  <h2>{campaign.summary.product ?? campaign.prompt}</h2>
                </div>
                <strong>{formatCurrency(campaign.summary.total_budget)}</strong>
              </div>

              <p className="campaign-card__objective">{campaign.summary.objective ?? campaign.prompt}</p>

              <div className="campaign-card__chips">
                <span>KPI: {campaign.summary.primary_kpi ?? 'pending'}</span>
                <span>{campaign.summary.channels?.length ?? 0} channels</span>
              </div>

              <div className="campaign-card__footer">
                <span>Updated {formatDate(campaign.updatedAt)}</span>
                <span>Open plan</span>
              </div>
            </Link>
          ))
        )}
        </div>
      </section>
    </section>
  )
}