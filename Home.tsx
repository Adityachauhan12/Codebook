import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { formatCurrency, formatDate } from '../lib/utils'
import { mockCampaigns } from '../lib/mockCampaigns'
import { loadCampaigns, loadChats, mergeCampaigns } from '../lib/storage'
import { fetchCampaigns } from '../lib/api'
import type { CampaignRecord } from '../types'
import {
  BarChart3,
  CircleDollarSign,
  Plus,
  Radio,
  Rocket,
} from 'lucide-react'

export function Home() {
  const navigate = useNavigate()
  const chats = loadChats()

  // Local first for an instant paint, then reconcile with the shared server list.
  const [storedCampaigns, setStoredCampaigns] = useState<CampaignRecord[]>(() => loadCampaigns())

  useEffect(() => {
    const controller = new AbortController()

    fetchCampaigns(controller.signal)
      .then((remote) => setStoredCampaigns((local) => mergeCampaigns(remote, local)))
      .catch(() => {
        /* Offline or backend down -- the local list stays on screen. */
      })

    return () => controller.abort()
  }, [])

  const campaigns = storedCampaigns.length > 0 ? storedCampaigns : mockCampaigns
  const topCampaigns = [...campaigns]
    .sort((left, right) => new Date(right.updatedAt).getTime() - new Date(left.updatedAt).getTime())
    .slice(0, 4)

  const runningChats = chats.filter((chat) => chat.status === 'running').length
  const completeChats = chats.filter((chat) => chat.status === 'complete').length
  const totalBudget = campaigns.reduce((total, campaign) => total + (campaign.summary.total_budget ?? 0), 0)

  const metrics = [
    { key: 'launches', label: 'Total campaigns', value: String(campaigns.length), icon: Rocket },
    { key: 'complete', label: 'Completed', value: String(completeChats), icon: BarChart3 },
    { key: 'live', label: 'Live sessions', value: String(runningChats), icon: Radio },
    { key: 'budget', label: 'Budget allocated', value: formatCurrency(totalBudget), icon: CircleDollarSign },
  ]

  function handleNewCampaign() {
    navigate('/history?new=1')
  }

  function getAlignmentTone(status: string | undefined) {
    if (status === 'aligned') return 'aligned'
    if (status === 'warnings') return 'warnings'
    return 'pending'
  }

  return (
    <section className="dashboard-shell">
      <header className="dashboard-hero">
        <div className="dashboard-hero__copy">
          <span className="eyebrow">Autonomous marketing OS</span>
          <h1>Plan, launch, and inspect campaign decisions in one operating view.</h1>
          <p>
            Track launch velocity, active agent processing, and budget allocation while starting a new brief in seconds.
          </p>
          <div className="dashboard-hero__actions">
            <button type="button" className="button button--primary" onClick={handleNewCampaign}>
              <Plus size={16} />
              New campaign
            </button>
            <button type="button" className="button button--ghost" onClick={() => navigate('/campaigns')}>
              View all campaigns
            </button>
          </div>
        </div>

        <div className="metric-strip">
          {metrics.map((metric) => {
            const Icon = metric.icon
            return (
              <article key={metric.key} className="metric-tile">
                <span className="metric-tile__icon">
                  <Icon size={16} />
                </span>
                <strong>{metric.value}</strong>
                <span className="metric-tile__label">{metric.label}</span>
              </article>
            )
          })}
        </div>
      </header>

      <section className="home-campaign-preview">
        <div className="home-campaign-preview__header">
          <div>
            <span className="eyebrow">Top campaigns</span>
            <h2>Latest plans, surfaced for quick review</h2>
          </div>
        </div>

        <div className="home-campaign-preview__grid">
          {topCampaigns.map((campaign) => (
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
          ))}
        </div>
      </section>

    </section>
  )
}