import { useState } from 'react'
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid,
  Tooltip, ResponsiveContainer,
} from 'recharts'
import { startBot, stopBot } from './api.js'

// ------------------------------------------------------------------ helpers

function fmt$(n) {
  if (n == null) return '—'
  const abs = Math.abs(n)
  const s = abs >= 1_000_000
    ? `$${(abs / 1_000_000).toFixed(2)}M`
    : abs >= 1_000
    ? `$${(abs / 1_000).toFixed(2)}K`
    : `$${abs.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
  return n < 0 ? `-${s}` : s
}

function fmtPct(n) {
  if (n == null) return '—'
  return `${(n * 100).toFixed(1)}%`
}

function fmtDate(iso) {
  if (!iso) return '—'
  return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
}

function fmtDateTime(iso) {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('en-US', {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  })
}

function PnlCell({ value }) {
  const cls = value > 0 ? 'green' : value < 0 ? 'red' : ''
  return <span className={cls}>{fmt$(value)}</span>
}

// ------------------------------------------------------------------ Header

function Header({ account, botStatus, onToggle, toggleLoading, onLogout, onRefresh }) {
  const running = botStatus?.running
  const mode = (botStatus?.mode || 'paper').toUpperCase()
  const isDry = botStatus?.dry_run

  return (
    <header className="header">
      <div className="header-left">
        <span className="logo-diamond">◆</span>
        <span className="logo-text">STOX</span>
        <span className={`badge badge-mode ${mode === 'LIVE' ? 'badge-live' : 'badge-paper'}`}>
          {mode}
        </span>
        {isDry && <span className="badge badge-dry">DRY RUN</span>}
      </div>

      <div className="header-right">
        <span className={`status-dot ${running ? 'dot-green' : 'dot-grey'}`} />
        <span className="status-label">{botStatus?.status ?? '—'}</span>

        <button
          className={`btn ${running ? 'btn-danger' : 'btn-primary'}`}
          onClick={() => onToggle(false)}
          disabled={toggleLoading}
        >
          {toggleLoading ? '…' : running ? 'Stop Bot' : 'Start Bot'}
        </button>

        {!running && (
          <button
            className="btn btn-secondary"
            onClick={() => onToggle(true)}
            disabled={toggleLoading}
            title="Start in dry-run mode (no orders placed)"
          >
            Dry Run
          </button>
        )}

        <button className="btn btn-ghost" onClick={onRefresh} title="Refresh now">
          ↺
        </button>
        <button className="btn btn-ghost" onClick={onLogout} title="Log out">
          ⏏
        </button>
      </div>
    </header>
  )
}

// ------------------------------------------------------------------ Stats row

function StatCard({ label, value, sub, valueClass }) {
  return (
    <div className="stat-card">
      <div className="stat-label">{label}</div>
      <div className={`stat-value ${valueClass || ''}`}>{value}</div>
      {sub && <div className="stat-sub">{sub}</div>}
    </div>
  )
}

function StatsRow({ account, summary, posCount }) {
  const pnl = summary?.total_pnl ?? 0
  const winRate = summary?.win_rate ?? null
  const totalTrades = summary?.total_trades ?? 0

  return (
    <div className="stats-row">
      <StatCard
        label="Portfolio Value"
        value={fmt$(account?.equity)}
        sub={`Cash ${fmt$(account?.cash)}`}
      />
      <StatCard
        label="Buying Power"
        value={fmt$(account?.buying_power)}
        sub={`Day trades: ${account?.daytrade_count ?? 0}`}
      />
      <StatCard
        label="Realised P&L"
        value={fmt$(pnl)}
        sub={`${totalTrades} closed trade${totalTrades !== 1 ? 's' : ''}`}
        valueClass={pnl > 0 ? 'green' : pnl < 0 ? 'red' : ''}
      />
      <StatCard
        label="Win Rate"
        value={winRate != null ? fmtPct(winRate) : '—'}
        sub={`PF: ${summary?.profit_factor === Infinity ? '∞' : (summary?.profit_factor ?? 0).toFixed(2)}x`}
      />
      <StatCard
        label="Open Positions"
        value={`${posCount} / 10`}
        sub={`Max ${10} concurrent`}
      />
    </div>
  )
}

// ------------------------------------------------------------------ Equity chart

function EquityChart({ snapshots }) {
  if (!snapshots || snapshots.length < 2) {
    return (
      <div className="card chart-card">
        <h2>Equity Curve</h2>
        <div className="empty-state">
          No equity history yet. The bot records a snapshot at market open
          and close each trading day.
        </div>
      </div>
    )
  }

  const data = snapshots.map((s) => ({
    date: fmtDate(s.timestamp),
    equity: parseFloat(s.equity.toFixed(2)),
  }))

  const minVal = Math.min(...data.map((d) => d.equity))
  const maxVal = Math.max(...data.map((d) => d.equity))
  const pad = (maxVal - minVal) * 0.1 || 100

  return (
    <div className="card chart-card">
      <h2>Equity Curve</h2>
      <ResponsiveContainer width="100%" height={220}>
        <AreaChart data={data} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
          <defs>
            <linearGradient id="eqGrad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor="#58a6ff" stopOpacity={0.25} />
              <stop offset="95%" stopColor="#58a6ff" stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="3 3" stroke="#21262d" />
          <XAxis dataKey="date" stroke="#8b949e" tick={{ fontSize: 11 }} />
          <YAxis
            stroke="#8b949e"
            tick={{ fontSize: 11 }}
            domain={[minVal - pad, maxVal + pad]}
            tickFormatter={(v) => `$${(v / 1000).toFixed(1)}K`}
            width={60}
          />
          <Tooltip
            formatter={(v) => [fmt$(v), 'Equity']}
            contentStyle={{ background: '#161b22', border: '1px solid #30363d', fontSize: 12 }}
            labelStyle={{ color: '#8b949e' }}
          />
          <Area
            type="monotone"
            dataKey="equity"
            stroke="#58a6ff"
            fill="url(#eqGrad)"
            strokeWidth={2}
            dot={false}
            activeDot={{ r: 4 }}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  )
}

// ------------------------------------------------------------------ Positions table

function PositionsTable({ positions }) {
  const rows = Object.entries(positions).map(([symbol, p]) => ({ symbol, ...p }))

  return (
    <div className="card table-card">
      <h2>Open Positions <span className="count-badge">{rows.length}</span></h2>
      {rows.length === 0 ? (
        <div className="empty-state">No open positions.</div>
      ) : (
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Symbol</th>
                <th>Qty</th>
                <th>Avg Entry</th>
                <th>Market Value</th>
                <th>Unrealised P&L</th>
                <th>P&L %</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((p) => (
                <tr key={p.symbol}>
                  <td className="symbol">{p.symbol}</td>
                  <td>{p.qty}</td>
                  <td>{fmt$(p.avg_entry)}</td>
                  <td>{fmt$(p.market_value)}</td>
                  <td><PnlCell value={p.unrealised_pl} /></td>
                  <td>
                    <span className={p.unrealised_plpc >= 0 ? 'green' : 'red'}>
                      {fmtPct(p.unrealised_plpc)}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

// ------------------------------------------------------------------ Trades table

const STATUS_CLASS = {
  OPEN: 'badge-open',
  CLOSED: 'badge-closed',
  STOPPED: 'badge-stopped',
  TOOK_PROFIT: 'badge-profit',
  SIGNAL_EXIT: 'badge-signal',
}

function TradesTable({ trades }) {
  return (
    <div className="card table-card">
      <h2>Recent Trades <span className="count-badge">{trades.length}</span></h2>
      {trades.length === 0 ? (
        <div className="empty-state">No trades recorded yet.</div>
      ) : (
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Symbol</th>
                <th>Shares</th>
                <th>Entry</th>
                <th>Exit</th>
                <th>P&L</th>
                <th>P&L %</th>
                <th>Status</th>
                <th>Opened</th>
              </tr>
            </thead>
            <tbody>
              {trades.map((t, i) => (
                <tr key={t.order_id || i}>
                  <td className="symbol">{t.symbol}</td>
                  <td>{t.shares}</td>
                  <td>{fmt$(t.entry_price)}</td>
                  <td>{t.exit_price ? fmt$(t.exit_price) : <span className="muted">open</span>}</td>
                  <td>{t.pnl != null ? <PnlCell value={t.pnl} /> : <span className="muted">—</span>}</td>
                  <td>
                    {t.pnl_pct != null
                      ? <span className={t.pnl_pct >= 0 ? 'green' : 'red'}>{fmtPct(t.pnl_pct)}</span>
                      : <span className="muted">—</span>}
                  </td>
                  <td>
                    <span className={`badge ${STATUS_CLASS[t.status] || ''}`}>
                      {t.status}
                    </span>
                  </td>
                  <td className="muted">{fmtDateTime(t.opened_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

// ------------------------------------------------------------------ Dashboard

export default function Dashboard({ data, onRefresh, onLogout, refreshError }) {
  const { account, positions, trades, summary, equityCurve, botStatus } = data
  const [toggleLoading, setToggleLoading] = useState(false)
  const [botMsg, setBotMsg] = useState(null)

  const handleToggle = async (dryRun = false) => {
    setToggleLoading(true)
    setBotMsg(null)
    try {
      const res = botStatus?.running
        ? await stopBot()
        : await startBot(dryRun)
      setBotMsg(res.data?.message)
      await onRefresh()
    } catch (err) {
      setBotMsg(err.response?.data?.detail || err.message)
    } finally {
      setToggleLoading(false)
    }
  }

  return (
    <div className="dashboard">
      <Header
        account={account}
        botStatus={botStatus}
        onToggle={handleToggle}
        toggleLoading={toggleLoading}
        onLogout={onLogout}
        onRefresh={onRefresh}
      />

      {(botMsg || refreshError || (botStatus?.status === 'error' && botStatus?.error)) && (
        <div className={`banner ${refreshError || botStatus?.status === 'error' ? 'banner-error' : 'banner-info'}`}>
          {refreshError || botMsg || `Bot error: ${botStatus?.error}`}
        </div>
      )}

      <main className="main">
        <StatsRow account={account} summary={summary} posCount={Object.keys(positions).length} />
        <EquityChart snapshots={equityCurve} />
        <div className="tables-grid">
          <PositionsTable positions={positions} />
          <TradesTable trades={trades.slice(0, 20)} />
        </div>
      </main>

      <footer className="footer">
        Auto-refreshes every 30 s &nbsp;·&nbsp; {new Date().toLocaleTimeString()}
      </footer>
    </div>
  )
}
