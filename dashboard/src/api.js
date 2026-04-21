import axios from 'axios'

let client = null

export function initClient(username, password) {
  const token = btoa(`${username}:${password}`)
  client = axios.create({ headers: { Authorization: `Bearer ${token}` } })
}

export async function fetchAll() {
  const [account, positions, trades, summary, equityCurve, botStatus] =
    await Promise.all([
      client.get('/api/account'),
      client.get('/api/positions'),
      client.get('/api/trades'),
      client.get('/api/summary'),
      client.get('/api/equity-curve'),
      client.get('/api/bot/status'),
    ])
  return {
    account: account.data,
    positions: positions.data,
    trades: trades.data.trades,
    summary: summary.data,
    equityCurve: equityCurve.data.snapshots,
    botStatus: botStatus.data,
  }
}

export async function startBot(dryRun = false) {
  return client.post(`/api/bot/start?dry_run=${dryRun}`)
}

export async function stopBot() {
  return client.post('/api/bot/stop')
}

export async function fetchLogs(hours = 72) {
  return client.get(`/api/logs?hours=${hours}`)
}

export async function fetchPendingTrades() {
  return client.get('/api/pending-trades')
}

export async function approveTrade(id) {
  return client.post(`/api/pending-trades/${id}/approve`)
}

export async function declineTrade(id) {
  return client.post(`/api/pending-trades/${id}/decline`)
}

export async function fetchPairs() {
  return client.get('/api/pairs')
}

export async function fetchAnalytics() {
  return client.get('/api/analytics')
}

export async function fetchRegime() {
  return client.get('/api/regime')
}

export async function fetchFeatures() {
  return client.get('/api/features')
}

export async function fetchMarket() {
  return client.get('/api/market')
}

export async function fetchMarketStatus() {
  return client.get('/api/market-status')
}

export async function fetchReview(days = 30) {
  return client.get(`/api/review?days=${days}`)
}

export async function fetchSettings() {
  return client.get('/api/settings')
}

export async function saveSettings(patch) {
  return client.patch('/api/settings', patch)
}

export async function fetchDailyAll() {
  const [account, positions, trades, status] = await Promise.all([
    client.get('/api/daily/account'),
    client.get('/api/daily/positions'),
    client.get('/api/daily/trades'),
    client.get('/api/daily/status'),
  ])
  return {
    account: account.data,
    positions: positions.data,
    trades: trades.data.trades,
    status: status.data,
  }
}

export async function startDailyBot(dryRun = false) {
  return client.post(`/api/daily/start?dry_run=${dryRun}`)
}

export async function stopDailyBot() {
  return client.post('/api/daily/stop')
}
