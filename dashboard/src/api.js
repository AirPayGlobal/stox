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

export async function fetchLogs(lines = 100) {
  return client.get(`/api/logs?lines=${lines}`)
}
