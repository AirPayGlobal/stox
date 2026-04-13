import { useState, useEffect, useRef } from 'react'
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid,
  Tooltip, ResponsiveContainer,
} from 'recharts'
import { startBot, stopBot, fetchLogs, fetchPendingTrades, approveTrade, declineTrade, fetchPairs, fetchAnalytics, fetchRegime, fetchFeatures, fetchMarket, fetchReview, fetchMarketStatus, fetchSettings, saveSettings } from './api.js'

// ------------------------------------------------------------------ helpers

function fmt$(n) {
  if (n == null) return '—'
  return n.toLocaleString('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 2, maximumFractionDigits: 2 })
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

// ------------------------------------------------------------------ Regime badge

const REGIME_COLORS = {
  BULL:     'regime-bull',
  RANGING:  'regime-ranging',
  HIGH_VOL: 'regime-highvol',
  BEAR:     'regime-bear',
}

function RegimeBadge() {
  const [regime, setRegime] = useState(null)

  useEffect(() => {
    const load = () =>
      fetchRegime()
        .then(r => setRegime(r.data))
        .catch(() => {})
    load()
    const id = setInterval(load, 300_000) // refresh every 5 min
    return () => clearInterval(id)
  }, [])

  if (!regime) return null

  return (
    <span
      className={`badge regime-badge ${REGIME_COLORS[regime.regime] || ''}`}
      title={`VIX ${regime.vix ?? '?'} · ADX ${regime.adx ?? '?'} · SPY ${regime.above_200 ? 'above' : 'below'} 200SMA`}
    >
      {regime.regime}
    </span>
  )
}

// ------------------------------------------------------------------ Header

function MarketStatusBadge() {
  const [isOpen, setIsOpen] = useState(null)

  useEffect(() => {
    const check = () =>
      fetchMarketStatus()
        .then(r => setIsOpen(r.data.is_open))
        .catch(() => setIsOpen(null))
    check()
    const id = setInterval(check, 60_000)
    return () => clearInterval(id)
  }, [])

  if (isOpen === null) return null
  return (
    <span
      className={`badge market-status-badge ${isOpen ? 'market-open' : 'market-closed'}`}
      title={isOpen ? 'US market is open — prices are live' : 'US market is closed — P&L reflects last close price (Alpaca may show extended-hours ticks)'}
    >
      {isOpen ? 'MKT OPEN' : 'MKT CLOSED'}
    </span>
  )
}

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
        <MarketStatusBadge />
        <RegimeBadge />
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
  const winningTrades = summary?.winning_trades ?? 0
  const totalWinPnl = summary?.total_win_pnl ?? 0

  return (
    <div className="stats-row">
      <StatCard
        label="Portfolio Value"
        value={fmt$(account?.equity)}
        sub={`Cash ${fmt$(account?.cash)}`}
      />
      <StatCard
        label="Deployable Capital"
        value={fmt$(account?.deployable_capital ?? account?.buying_power)}
        sub={`Capped at ${fmt$(account?.base_capital ?? 100000)} base`}
      />
      <StatCard
        label="Withdrawable Profit"
        value={fmt$(account?.withdrawable_cash ?? 0)}
        sub={account?.withdrawal_alert
          ? '⚠ Withdraw from Alpaca now'
          : `Equity above $${((account?.base_capital ?? 100000) / 1000).toFixed(0)}k base`}
        valueClass={(account?.withdrawable_cash ?? 0) > 0 ? 'green' : ''}
      />
      <StatCard
        label="Realised P&L"
        value={fmt$(pnl)}
        sub={totalTrades > 0
          ? `${totalTrades} closed · ${winningTrades} winners (${fmt$(totalWinPnl)})`
          : 'No closed trades yet'}
        valueClass={pnl > 0 ? 'green' : pnl < 0 ? 'red' : ''}
      />
      <StatCard
        label="Win Rate"
        value={winRate != null ? fmtPct(winRate) : '—'}
        sub={`PF: ${summary?.profit_factor === Infinity ? '∞' : (summary?.profit_factor ?? 0).toFixed(2)}x`}
      />
      <StatCard
        label="Open Positions"
        value={`${posCount} open`}
        sub="Max 20 concurrent"
      />
    </div>
  )
}

// ------------------------------------------------------------------ Equity chart

function EquityChart({ snapshots, account }) {
  const liveEquity = account?.equity

  // Build full data set first so the live "Now" point counts toward the minimum
  const baseData = (snapshots || []).map((s) => ({
    date: fmtDate(s.timestamp),
    equity: parseFloat(s.equity.toFixed(2)),
    isLive: false,
  }))
  const data = liveEquity != null
    ? [...baseData, { date: 'Now', equity: parseFloat(Number(liveEquity).toFixed(2)), isLive: true }]
    : baseData

  if (data.length === 0) {
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
            dot={(props) => {
              if (!props.payload?.isLive) return null
              return (
                <circle
                  key="live-dot"
                  cx={props.cx}
                  cy={props.cy}
                  r={5}
                  fill="#3fb950"
                  stroke="#0d1117"
                  strokeWidth={2}
                />
              )
            }}
            activeDot={{ r: 4 }}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  )
}

// ------------------------------------------------------------------ Positions table

function TpCell({ tp, currentPrice, entry, side }) {
  if (!tp || !currentPrice || !entry) return <span className="muted">—</span>

  const isShort = side === 'short'

  // For longs:  TP above entry, hit when price rises to tp
  // For shorts: TP below entry, hit when price falls to tp
  const hit     = isShort ? currentPrice <= tp : currentPrice >= tp
  const pctToTp = isShort
    ? (currentPrice - tp) / currentPrice          // how far still needs to fall
    : (tp - currentPrice) / currentPrice          // how far still needs to rise
  const totalPct = isShort
    ? (entry - tp) / entry
    : (tp - entry) / entry
  const progress = isShort
    ? Math.min(1, Math.max(0, (entry - currentPrice) / (entry - tp)))
    : Math.min(1, Math.max(0, (currentPrice - entry) / (tp - entry)))
  const isClose  = pctToTp <= 0.03

  if (hit) return <span className="badge badge-profit">TP Hit</span>

  return (
    <div className="tp-cell">
      <div className="tp-price">
        {fmt$(tp)}
        <span className={`tp-pct ${isClose ? 'green' : 'muted'}`}>
          +{fmtPct(pctToTp)} away
        </span>
      </div>
      <div className="tp-bar-track">
        <div className="tp-bar-fill" style={{ width: `${(progress * 100).toFixed(1)}%` }} />
      </div>
      <div className="tp-sub muted">{fmtPct(totalPct)} total target</div>
    </div>
  )
}

function EarningsBadge({ days }) {
  if (days == null) return null
  if (days <= 2)  return <span className="earnings-badge earnings-imminent">Earnings in {days}d</span>
  if (days <= 7)  return <span className="earnings-badge earnings-soon">Earnings in {days}d</span>
  if (days <= 21) return <span className="earnings-badge earnings-upcoming">Earnings in {days}d</span>
  return null
}

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
                <th>Take Profit Target</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((p) => {
                const currentPrice = p.qty ? p.market_value / p.qty : null
                return (
                  <tr key={p.symbol}>
                    <td className="symbol">
                      {p.symbol}
                      <EarningsBadge days={p.days_to_earnings} />
                    </td>
                    <td>{p.qty}</td>
                    <td>{fmt$(p.avg_entry)}</td>
                    <td>{fmt$(p.market_value)}</td>
                    <td><PnlCell value={p.unrealised_pl} /></td>
                    <td>
                      <span className={p.unrealised_plpc >= 0 ? 'green' : 'red'}>
                        {fmtPct(p.unrealised_plpc)}
                      </span>
                    </td>
                    <td>
                      <TpCell tp={p.take_profit} currentPrice={currentPrice} entry={p.avg_entry} side={p.side} />
                    </td>
                  </tr>
                )
              })}
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
  TRAILING_STOP: 'badge-trail',
  BREAK_EVEN: 'badge-breakeven',
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

// ------------------------------------------------------------------ History tab

function fmtDuration(openedAt, closedAt) {
  if (!openedAt || !closedAt) return '—'
  const ms = new Date(closedAt) - new Date(openedAt)
  if (ms < 0) return '—'
  const mins = Math.floor(ms / 60000)
  if (mins < 60) return `${mins}m`
  const hours = Math.floor(mins / 60)
  if (hours < 24) return `${hours}h ${mins % 60}m`
  const days = Math.floor(hours / 24)
  return `${days}d ${hours % 24}h`
}

const EXIT_LABELS = {
  CLOSED:        'Closed',
  STOPPED:       'Stop Loss',
  TOOK_PROFIT:   'Take Profit',
  TRAILING_STOP: 'Trailing Stop',
  BREAK_EVEN:    'Break-Even',
  SIGNAL_EXIT:   'Signal Exit',
}

function HistoryTab({ trades }) {
  const [timeFilter, setTimeFilter] = useState('today')
  const [exitFilter, setExitFilter] = useState('all')

  const closed = trades.filter(t => t.status !== 'OPEN')
  const today = new Date().toISOString().slice(0, 10)
  const weekAgo = new Date(Date.now() - 7 * 86400000).toISOString().slice(0, 10)

  const timeFiltered = closed.filter(t => {
    if (!t.closed_at) return false
    const d = t.closed_at.slice(0, 10)
    if (timeFilter === 'today') return d === today
    if (timeFilter === 'week') return d >= weekAgo
    return true
  })

  const filtered = exitFilter === 'all'
    ? timeFiltered
    : timeFiltered.filter(t => t.status === exitFilter)

  const todayTrades = closed.filter(t => t.closed_at?.slice(0, 10) === today)
  const todayPnl = todayTrades.reduce((s, t) => s + (t.pnl || 0), 0)
  const todayWins = todayTrades.filter(t => (t.pnl || 0) > 0).length

  return (
    <div className="history-tab">
      <div className="history-summary">
        <div className="history-stat">
          <span className="history-stat-label">Closed Today</span>
          <span className="history-stat-value">{todayTrades.length}</span>
        </div>
        <div className="history-stat">
          <span className="history-stat-label">Today P&L</span>
          <span className={`history-stat-value ${todayPnl >= 0 ? 'green' : 'red'}`}>{fmt$(todayPnl)}</span>
        </div>
        <div className="history-stat">
          <span className="history-stat-label">Today Win / Loss</span>
          <span className="history-stat-value">
            <span className="green">{todayWins}W</span>
            {' / '}
            <span className="red">{todayTrades.length - todayWins}L</span>
          </span>
        </div>
        <div className="history-stat">
          <span className="history-stat-label">All-Time Closed</span>
          <span className="history-stat-value">{closed.length}</span>
        </div>
      </div>

      <div className="history-filters">
        <div className="filter-group">
          {[
            { key: 'today', label: 'Today' },
            { key: 'week',  label: 'This Week' },
            { key: 'all',   label: 'All Time' },
          ].map(({ key, label }) => (
            <button
              key={key}
              className={`filter-btn ${timeFilter === key ? 'filter-btn-active' : ''}`}
              onClick={() => setTimeFilter(key)}
            >
              {label}
            </button>
          ))}
        </div>
        <div className="filter-group">
          {[
            { key: 'all',           label: 'All Exits' },
            { key: 'TOOK_PROFIT',   label: 'Take Profit' },
            { key: 'TRAILING_STOP', label: 'Trailing Stop' },
            { key: 'STOPPED',       label: 'Stop Loss' },
            { key: 'BREAK_EVEN',    label: 'Break-Even' },
            { key: 'SIGNAL_EXIT',   label: 'Signal Exit' },
            { key: 'CLOSED',        label: 'Manual' },
          ].map(({ key, label }) => (
            <button
              key={key}
              className={`filter-btn ${exitFilter === key ? 'filter-btn-active' : ''}`}
              onClick={() => setExitFilter(key)}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      <div className="card table-card">
        <h2>
          Closed Trades
          <span className="count-badge">{filtered.length}</span>
          {timeFilter === 'today' && todayTrades.length === 0 && (
            <span className="history-none-today">— none closed today</span>
          )}
        </h2>
        {filtered.length === 0 ? (
          <div className="empty-state">No closed trades match this filter.</div>
        ) : (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Symbol</th>
                  <th>Side</th>
                  <th>Shares</th>
                  <th>Entry</th>
                  <th>Exit</th>
                  <th>P&L</th>
                  <th>P&L %</th>
                  <th>Exit Reason</th>
                  <th>Opened</th>
                  <th>Closed</th>
                  <th>Duration</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((t, i) => (
                  <tr key={t.order_id || i} className={(t.pnl || 0) >= 0 ? 'row-win' : 'row-loss'}>
                    <td className="symbol">{t.symbol}</td>
                    <td>
                      <span className={`side-badge side-${(t.side || 'BUY').toLowerCase()}`}>
                        {t.side === 'SHORT' ? 'SHORT' : 'LONG'}
                      </span>
                    </td>
                    <td>{t.shares}</td>
                    <td>{fmt$(t.entry_price)}</td>
                    <td>{fmt$(t.exit_price)}</td>
                    <td>{t.pnl != null ? <PnlCell value={t.pnl} /> : <span className="muted">—</span>}</td>
                    <td>
                      {t.pnl_pct != null
                        ? <span className={t.pnl_pct >= 0 ? 'green' : 'red'}>{fmtPct(t.pnl_pct)}</span>
                        : <span className="muted">—</span>}
                    </td>
                    <td>
                      <span className={`badge ${STATUS_CLASS[t.status] || ''}`}>
                        {EXIT_LABELS[t.status] || t.status}
                      </span>
                    </td>
                    <td className="muted">{fmtDateTime(t.opened_at)}</td>
                    <td className="muted">{fmtDateTime(t.closed_at)}</td>
                    <td className="muted">{fmtDuration(t.opened_at, t.closed_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}

// ------------------------------------------------------------------ Pairs Panel

function PairsPanel() {
  const [pairs, setPairs] = useState([])
  const [summary, setSummary] = useState(null)
  const [open, setOpen] = useState(true)

  useEffect(() => {
    const load = () =>
      fetchPairs()
        .then(r => { setPairs(r.data.pairs); setSummary(r.data.summary) })
        .catch(() => {})
    load()
    const id = setInterval(load, 30_000)
    return () => clearInterval(id)
  }, [])

  if (!pairs.length) return null

  const openPairs  = pairs.filter(p => p.status === 'open')
  const closedPairs = pairs.filter(p => p.status === 'closed').slice(0, 5)

  return (
    <div className="card pairs-panel">
      <div className="pairs-header" onClick={() => setOpen(o => !o)}>
        <h2>
          Pairs / Stat-Arb
          <span className="count-badge">{openPairs.length} open</span>
          {summary && (
            <span className="pairs-summary">
              Total P&L: <span className={summary.total_pnl >= 0 ? 'green' : 'red'}>{fmt$(summary.total_pnl)}</span>
              &nbsp;·&nbsp;Win rate: {summary.closed_pairs > 0 ? (summary.win_rate * 100).toFixed(0) : '—'}%
            </span>
          )}
        </h2>
        <span className="log-toggle">{open ? '▲' : '▼'}</span>
      </div>

      {open && (
        <>
          {openPairs.length > 0 && (
            <div className="table-scroll" style={{ marginBottom: 12 }}>
              <table>
                <thead>
                  <tr>
                    <th>Pair</th>
                    <th>Long</th>
                    <th>Short</th>
                    <th>Entry Z</th>
                    <th>Opened</th>
                  </tr>
                </thead>
                <tbody>
                  {openPairs.map(p => (
                    <tr key={p.pair_id}>
                      <td className="symbol">{p.symbol_a}/{p.symbol_b}</td>
                      <td><span className="green">{p.qty_long}×{p.symbol_long}</span> @ {fmt$(p.price_long)}</td>
                      <td><span className="red">{p.qty_short}×{p.symbol_short}</span> @ {fmt$(p.price_short)}</td>
                      <td className="mono">{p.entry_z?.toFixed(2)}</td>
                      <td className="muted">{fmtDateTime(p.opened_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {closedPairs.length > 0 && (
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>Pair</th>
                    <th>P&L</th>
                    <th>Exit Z</th>
                    <th>Reason</th>
                    <th>Closed</th>
                  </tr>
                </thead>
                <tbody>
                  {closedPairs.map(p => (
                    <tr key={p.pair_id}>
                      <td className="symbol">{p.symbol_a}/{p.symbol_b}</td>
                      <td><PnlCell value={p.pnl} /></td>
                      <td className="mono">{p.exit_z?.toFixed(2)}</td>
                      <td><span className={`badge ${p.close_reason === 'MEAN_REVERSION' ? 'badge-profit' : 'badge-stopped'}`}>
                        {p.close_reason}
                      </span></td>
                      <td className="muted">{fmtDateTime(p.closed_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </div>
  )
}

// ------------------------------------------------------------------ IPO Approval Panel

function Countdown({ expiresAt }) {
  const [left, setLeft] = useState('')

  useEffect(() => {
    const tick = () => {
      const diff = new Date(expiresAt) - Date.now()
      if (diff <= 0) { setLeft('Executing now…'); return }
      const m = Math.floor(diff / 60000)
      const s = Math.floor((diff % 60000) / 1000)
      setLeft(`${m}m ${s.toString().padStart(2, '0')}s`)
    }
    tick()
    const id = setInterval(tick, 1000)
    return () => clearInterval(id)
  }, [expiresAt])

  return <span className="countdown">{left}</span>
}

function IPOApprovalPanel() {
  const [trades, setTrades] = useState([])
  const [acting, setActing] = useState({})

  const load = () =>
    fetchPendingTrades()
      .then(r => setTrades(r.data.trades))
      .catch(() => {})

  useEffect(() => {
    load()
    const id = setInterval(load, 15_000)
    return () => clearInterval(id)
  }, [])

  const handle = async (id, action) => {
    setActing(a => ({ ...a, [id]: action }))
    try {
      action === 'approve' ? await approveTrade(id) : await declineTrade(id)
      await load()
    } catch (e) {
      alert(e.response?.data?.detail || e.message)
    } finally {
      setActing(a => ({ ...a, [id]: null }))
    }
  }

  if (trades.length === 0) return null

  return (
    <div className="card ipo-panel">
      <h2>
        IPO Trade Approvals
        <span className="count-badge">{trades.length}</span>
        <span className="ipo-panel-sub">Auto-executes if no response within 60 min</span>
      </h2>
      {trades.map(t => (
        <div key={t.id} className="ipo-card">
          <div className="ipo-card-header">
            <span className="ipo-symbol">{t.symbol}</span>
            <span className="badge badge-ipo">{t.trade_type}</span>
            <span className="ipo-score">score {t.score}</span>
            <span className="ipo-timer">
              Auto-executes in <Countdown expiresAt={t.expires_at} />
            </span>
          </div>
          {t.headline && (
            <div className="ipo-headline">"{t.headline}"</div>
          )}
          <div className="ipo-details">
            <span>{t.shares} shares @ {fmt$(t.price)}</span>
            <span className="muted">·</span>
            <span>SL <span className="red">{fmt$(t.stop_loss)}</span></span>
            <span className="muted">·</span>
            <span>TP <span className="green">{fmt$(t.take_profit)}</span></span>
            <span className="muted">·</span>
            <span>Cost <strong>{fmt$(t.shares * t.price)}</strong></span>
          </div>
          <div className="ipo-actions">
            <button
              className="btn btn-primary"
              disabled={!!acting[t.id]}
              onClick={() => handle(t.id, 'approve')}
            >
              {acting[t.id] === 'approve' ? '…' : '✓ Accept'}
            </button>
            <button
              className="btn btn-danger"
              disabled={!!acting[t.id]}
              onClick={() => handle(t.id, 'decline')}
            >
              {acting[t.id] === 'decline' ? '…' : '✗ Decline'}
            </button>
          </div>
        </div>
      ))}
    </div>
  )
}

// ------------------------------------------------------------------ Analytics Panel

function AnalyticsPanel() {
  const [data, setData] = useState(null)
  const [open, setOpen] = useState(true)

  useEffect(() => {
    const load = () =>
      fetchAnalytics()
        .then(r => setData(r.data))
        .catch(() => {})
    load()
    const id = setInterval(load, 60_000)
    return () => clearInterval(id)
  }, [])

  if (!data || data.days_tracked < 5) return null

  const curve = data.equity_curve || []
  const minV  = curve.length ? Math.min(...curve.map(d => d.equity)) : 0
  const maxV  = curve.length ? Math.max(...curve.map(d => d.equity)) : 0
  const pad   = (maxV - minV) * 0.12 || 100

  return (
    <div className="card analytics-panel">
      <div className="pairs-header" onClick={() => setOpen(o => !o)}>
        <h2>
          Risk Analytics
          <span className="count-badge">{data.days_tracked}d</span>
        </h2>
        <span className="log-toggle">{open ? '▲' : '▼'}</span>
      </div>

      {open && (
        <>
          <div className="analytics-metrics">
            <div className="analytics-metric">
              <span className="analytics-label">Sharpe</span>
              <span className={`analytics-value ${data.sharpe >= 1 ? 'green' : data.sharpe < 0 ? 'red' : ''}`}>
                {data.sharpe ?? '—'}
              </span>
            </div>
            <div className="analytics-metric">
              <span className="analytics-label">Sortino</span>
              <span className={`analytics-value ${data.sortino >= 1 ? 'green' : data.sortino < 0 ? 'red' : ''}`}>
                {data.sortino ?? '—'}
              </span>
            </div>
            <div className="analytics-metric">
              <span className="analytics-label">Calmar</span>
              <span className={`analytics-value ${data.calmar >= 0.5 ? 'green' : ''}`}>
                {data.calmar ?? '—'}
              </span>
            </div>
            <div className="analytics-metric">
              <span className="analytics-label">Max DD</span>
              <span className="analytics-value red">
                {data.max_drawdown_pct != null ? `${data.max_drawdown_pct}%` : '—'}
              </span>
            </div>
            <div className="analytics-metric">
              <span className="analytics-label">VaR 95%</span>
              <span className="analytics-value">
                {data.var_95_pct != null ? `${data.var_95_pct}%` : '—'}
              </span>
            </div>
            <div className="analytics-metric">
              <span className="analytics-label">Total Return</span>
              <span className={`analytics-value ${data.total_return_pct >= 0 ? 'green' : 'red'}`}>
                {data.total_return_pct != null ? `${data.total_return_pct}%` : '—'}
              </span>
            </div>
            {data.win_rate != null && (
              <div className="analytics-metric">
                <span className="analytics-label">Win Rate</span>
                <span className="analytics-value">{data.win_rate}%</span>
              </div>
            )}
            {data.profit_factor != null && (
              <div className="analytics-metric">
                <span className="analytics-label">Profit Factor</span>
                <span className={`analytics-value ${data.profit_factor >= 1.5 ? 'green' : data.profit_factor < 1 ? 'red' : ''}`}>
                  {data.profit_factor}x
                </span>
              </div>
            )}
          </div>

          {curve.length >= 3 && (
            <ResponsiveContainer width="100%" height={160}>
              <AreaChart data={curve} margin={{ top: 4, right: 16, left: 0, bottom: 0 }}>
                <defs>
                  <linearGradient id="anlGrad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%"  stopColor="#3fb950" stopOpacity={0.25} />
                    <stop offset="95%" stopColor="#3fb950" stopOpacity={0}    />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="#21262d" />
                <XAxis dataKey="date" stroke="#8b949e" tick={{ fontSize: 10 }} />
                <YAxis
                  stroke="#8b949e"
                  tick={{ fontSize: 10 }}
                  domain={[minV - pad, maxV + pad]}
                  tickFormatter={v => `$${(v / 1000).toFixed(1)}K`}
                  width={58}
                />
                <Tooltip
                  formatter={v => [fmt$(v), 'Equity']}
                  contentStyle={{ background: '#161b22', border: '1px solid #30363d', fontSize: 12 }}
                  labelStyle={{ color: '#8b949e' }}
                />
                <Area
                  type="monotone"
                  dataKey="equity"
                  stroke="#3fb950"
                  fill="url(#anlGrad)"
                  strokeWidth={2}
                  dot={false}
                />
              </AreaChart>
            </ResponsiveContainer>
          )}
        </>
      )}
    </div>
  )
}

// ------------------------------------------------------------------ Market Panel

function Ticker({ symbol, data, highlight }) {
  if (!data) return null
  const up = data.change_pct >= 0
  return (
    <div className={`ticker-card ${highlight ? 'ticker-highlight' : ''}`}>
      <span className="ticker-sym">{symbol}</span>
      <span className="ticker-price">{fmt$(data.price)}</span>
      <span className={`ticker-chg ${up ? 'green' : 'red'}`}>
        {up ? '+' : ''}{data.change_pct?.toFixed(2)}%
      </span>
    </div>
  )
}

function SectorBar({ sector, max }) {
  const pct = sector.change_pct
  const up  = pct >= 0
  const barW = Math.min(Math.abs(pct) / max * 100, 100)
  return (
    <div className="sector-row">
      <span className="sector-sym">{sector.symbol}</span>
      <span className="sector-name">{sector.name}</span>
      <div className="sector-bar-wrap">
        <div
          className={`sector-bar ${up ? 'sector-bar-up' : 'sector-bar-dn'}`}
          style={{ width: `${barW}%` }}
        />
      </div>
      <span className={`sector-pct ${up ? 'green' : 'red'}`}>
        {up ? '+' : ''}{pct.toFixed(2)}%
      </span>
    </div>
  )
}

function FilterPill({ label, value, blocking }) {
  return (
    <div className={`filter-pill ${blocking ? 'filter-pill-block' : 'filter-pill-ok'}`}>
      <span className="filter-label">{label}</span>
      <span className="filter-val">{value}</span>
    </div>
  )
}

function MarketPanel() {
  const [data, setData] = useState(null)
  const [open, setOpen] = useState(true)
  const [lastUpdate, setLastUpdate] = useState(null)

  useEffect(() => {
    const load = () =>
      fetchMarket()
        .then(r => { setData(r.data); setLastUpdate(new Date()) })
        .catch(() => {})
    load()
    const id = setInterval(load, 60_000) // refresh every 60s
    return () => clearInterval(id)
  }, [])

  if (!data) return null

  const { indices, vix, sectors, filter_state: fs, watchlist_snapshot: snap } = data
  const maxSectorAbs = sectors.length
    ? Math.max(...sectors.map(s => Math.abs(s.change_pct)), 0.1)
    : 1

  return (
    <div className="card market-panel">
      <div className="market-header" onClick={() => setOpen(o => !o)}>
        <h2>
          Live Market
          {vix && (
            <span
              className={`vix-badge ${vix.price > 30 ? 'vix-high' : vix.price > 20 ? 'vix-mid' : 'vix-low'}`}
              title="CBOE Volatility Index"
            >
              VIX {vix.price}
            </span>
          )}
        </h2>
        {lastUpdate && (
          <span className="market-updated">
            updated {lastUpdate.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
          </span>
        )}
        <span className="log-toggle">{open ? '▲' : '▼'}</span>
      </div>

      {open && (
        <div className="market-body">

          {/* Indices row */}
          <div className="market-section">
            <div className="market-section-label">Indices</div>
            <div className="ticker-row">
              {Object.entries(indices || {}).map(([sym, d]) => (
                <Ticker key={sym} symbol={sym} data={d} />
              ))}
              {vix && <Ticker symbol="VIX" data={vix} />}
            </div>
          </div>

          {/* Bot filter state */}
          {fs && Object.keys(fs).length > 0 && (
            <div className="market-section">
              <div className="market-section-label">Bot Filters</div>
              <div className="filter-pills">
                <FilterPill
                  label="VIX"
                  value={`${fs.vix_value ?? '?'} / ${fs.vix_threshold} threshold`}
                  blocking={fs.vix_blocking}
                />
                <FilterPill
                  label="Regime"
                  value={`${fs.regime ?? '?'} · ${fs.regime_sizing ?? '?'} size`}
                  blocking={fs.regime === 'HIGH_VOL'}
                />
                <FilterPill
                  label="ML Signal"
                  value={fs.ml_enabled ? `p ≥ ${fs.ml_min_prob}` : 'disabled'}
                  blocking={!fs.ml_enabled}
                />
                <FilterPill
                  label="Weekly Confirm"
                  value={fs.weekly_req ? 'required' : 'off'}
                  blocking={false}
                />
                <FilterPill
                  label="Sector Filter"
                  value={`top ${fs.sector_top_n} sectors`}
                  blocking={false}
                />
                <FilterPill
                  label="Kelly Sizing"
                  value={fs.kelly_active ? 'active' : 'warmup'}
                  blocking={false}
                />
                <FilterPill
                  label="Short Selling"
                  value={fs.short_enabled ? 'enabled' : 'disabled'}
                  blocking={false}
                />
              </div>
            </div>
          )}

          <div className="market-columns">
            {/* Sector heatmap */}
            {sectors.length > 0 && (
              <div className="market-section market-section-sectors">
                <div className="market-section-label">Sectors (1d)</div>
                <div className="sector-list">
                  {sectors.map(s => (
                    <SectorBar key={s.symbol} sector={s} max={maxSectorAbs} />
                  ))}
                </div>
              </div>
            )}

            {/* Watchlist snapshot */}
            {snap.length > 0 && (
              <div className="market-section market-section-snap">
                <div className="market-section-label">Watchlist Snapshot</div>
                <div className="snap-list">
                  {snap.map(s => (
                    <div key={s.symbol} className={`snap-row ${s.is_open ? 'snap-open' : ''}`}>
                      <span className="snap-sym">
                        {s.is_open && <span className="snap-dot">●</span>}
                        {s.symbol}
                      </span>
                      <span className="snap-price">{fmt$(s.price)}</span>
                      <span className={`snap-chg ${s.change_pct >= 0 ? 'green' : 'red'}`}>
                        {s.change_pct >= 0 ? '+' : ''}{s.change_pct?.toFixed(2)}%
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>

        </div>
      )}
    </div>
  )
}

// ------------------------------------------------------------------ Features Panel

const TIER_COLORS = { 1: '#58a6ff', 2: '#bc8cff', 3: '#3fb950' }
const TIER_LABELS = { 1: 'Tier 1', 2: 'Tier 2', 3: 'Tier 3' }

const STATUS_ICON = {
  active:   { icon: '●', cls: 'feat-active'   },
  blocking: { icon: '◉', cls: 'feat-blocking' },
  warmup:   { icon: '◎', cls: 'feat-warmup'   },
  disabled: { icon: '○', cls: 'feat-disabled' },
}

function FeatureCard({ feature, tierColor }) {
  const st = STATUS_ICON[feature.status] || STATUS_ICON.active
  return (
    <div className={`feat-card ${feature.enabled ? '' : 'feat-card-off'}`}>
      <div className="feat-card-top">
        <span className={`feat-dot ${st.cls}`} title={feature.status}>{st.icon}</span>
        <span className="feat-name">{feature.name}</span>
        <span
          className={`feat-status-badge feat-status-${feature.status}`}
        >
          {feature.status}
        </span>
      </div>
      <div className="feat-desc">{feature.description}</div>
      <div className="feat-footer">
        {feature.live && (
          <span className="feat-live" style={{ color: tierColor }}>
            ▸ {feature.live}
          </span>
        )}
        <span className="feat-config">{feature.config}</span>
      </div>
    </div>
  )
}

function FeaturesPanel() {
  const [data, setData]   = useState(null)
  const [open, setOpen]   = useState(false)

  useEffect(() => {
    const load = () =>
      fetchFeatures()
        .then(r => setData(r.data))
        .catch(() => {})
    load()
    const id = setInterval(load, 60_000)
    return () => clearInterval(id)
  }, [])

  if (!data) return null

  const totalFeatures  = data.tiers.reduce((n, t) => n + t.features.length, 0)
  const activeFeatures = data.tiers.reduce(
    (n, t) => n + t.features.filter(f => f.status !== 'disabled').length, 0
  )

  return (
    <div className="card feat-panel">
      <div className="feat-panel-header" onClick={() => setOpen(o => !o)}>
        <h2>
          Strategy Features
          <span className="count-badge">{activeFeatures} / {totalFeatures} active</span>
        </h2>
        <div className="feat-tier-pills">
          {data.tiers.map(t => (
            <span
              key={t.tier}
              className="feat-tier-pill"
              style={{ borderColor: TIER_COLORS[t.tier], color: TIER_COLORS[t.tier] }}
            >
              {TIER_LABELS[t.tier]} · {t.features.length}
            </span>
          ))}
        </div>
        <span className="log-toggle">{open ? '▲' : '▼'}</span>
      </div>

      {open && (
        <div className="feat-tiers">
          {data.tiers.map(t => (
            <div key={t.tier} className="feat-tier">
              <div
                className="feat-tier-heading"
                style={{ borderLeftColor: TIER_COLORS[t.tier], color: TIER_COLORS[t.tier] }}
              >
                <span className="feat-tier-label">{TIER_LABELS[t.tier]}</span>
                <span className="feat-tier-name">{t.label}</span>
                <span className="feat-tier-count">
                  {t.features.filter(f => f.status !== 'disabled').length} active
                </span>
              </div>
              <div className="feat-grid">
                {t.features.map(f => (
                  <FeatureCard key={f.name} feature={f} tierColor={TIER_COLORS[t.tier]} />
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ------------------------------------------------------------------ LogViewer

function LogViewer() {
  const [logs, setLogs] = useState([])
  const [open, setOpen] = useState(true)
  const bodyRef = useRef(null)

  useEffect(() => {
    const load = () => fetchLogs(150).then(r => setLogs(r.data.lines)).catch(() => {})
    load()
    const id = setInterval(load, 10_000)
    return () => clearInterval(id)
  }, [])

  useEffect(() => {
    if (open && bodyRef.current) {
      bodyRef.current.scrollTop = bodyRef.current.scrollHeight
    }
  }, [logs])

  return (
    <div className="log-viewer">
      <div className="log-header" onClick={() => setOpen(o => !o)}>
        <span>Bot Logs</span>
        <span className="log-toggle">{open ? '▲' : '▼'}</span>
      </div>
      {open && (
        <div className="log-body" ref={bodyRef}>
          {logs.length === 0
            ? <span className="log-empty">No logs yet.</span>
            : logs.map((line, i) => {
                const level = line.includes('| ERROR') ? 'log-error'
                  : line.includes('| WARNING') ? 'log-warn'
                  : line.includes('| INFO') ? 'log-info'
                  : 'log-debug'
                return <div key={i} className={`log-line ${level}`}>{line}</div>
              })
          }
        </div>
      )}
    </div>
  )
}


// ------------------------------------------------------------------ Settings Tab

function SettingsTab({ account }) {
  const [values, setValues] = useState(null)
  const [saving, setSaving] = useState(false)
  const [msg, setMsg] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    fetchSettings()
      .then(r => setValues(r.data))
      .catch(() => setError('Could not load settings'))
  }, [])

  const handleChange = (key, raw) => {
    setValues(v => ({ ...v, [key]: raw }))
    setMsg(null)
    setError(null)
  }

  const handleSave = async () => {
    setSaving(true)
    setMsg(null)
    setError(null)
    try {
      const patch = {
        BASE_CAPITAL: parseFloat(values.BASE_CAPITAL),
        PROFIT_WITHDRAWAL_ALERT_PCT: parseFloat(values.PROFIT_WITHDRAWAL_ALERT_PCT),
      }
      await saveSettings(patch)
      setMsg('Settings saved — applied immediately to the running bot.')
    } catch (err) {
      setError(err.response?.data?.detail || err.message)
    } finally {
      setSaving(false)
    }
  }

  const withdrawable = account?.withdrawable_cash ?? 0
  const alertPct = values ? parseFloat(values.PROFIT_WITHDRAWAL_ALERT_PCT) * 100 : 10
  const alertAt = values ? parseFloat(values.BASE_CAPITAL) * parseFloat(values.PROFIT_WITHDRAWAL_ALERT_PCT) : 0

  return (
    <div className="settings-tab">
      <h2 className="settings-header">Capital Settings</h2>
      <p className="settings-desc">
        The bot only deploys up to <strong>Base Capital</strong>. Any equity above that
        accumulates as cash and is never reinvested — withdraw it from Alpaca whenever you like.
      </p>

      {error && <div className="settings-error">{error}</div>}
      {msg   && <div className="settings-success">{msg}</div>}

      {values ? (
        <div className="settings-form">
          <div className="settings-field">
            <label className="settings-label">
              Base Capital
              <span className="settings-hint">Amount the bot is authorised to trade with</span>
            </label>
            <div className="settings-input-wrap">
              <span className="settings-prefix">$</span>
              <input
                type="number"
                className="settings-input"
                value={values.BASE_CAPITAL}
                min="1000"
                step="1000"
                onChange={e => handleChange('BASE_CAPITAL', e.target.value)}
              />
            </div>
          </div>

          <div className="settings-field">
            <label className="settings-label">
              Withdrawal Alert Threshold
              <span className="settings-hint">
                Alert when withdrawable profit exceeds this % of base capital
                {values ? ` (= ${fmt$(alertAt)})` : ''}
              </span>
            </label>
            <div className="settings-input-wrap">
              <input
                type="number"
                className="settings-input"
                value={(parseFloat(values.PROFIT_WITHDRAWAL_ALERT_PCT) * 100).toFixed(1)}
                min="1"
                max="100"
                step="1"
                onChange={e => handleChange('PROFIT_WITHDRAWAL_ALERT_PCT', (parseFloat(e.target.value) / 100).toString())}
              />
              <span className="settings-suffix">%</span>
            </div>
          </div>

          <div className="settings-summary">
            <div className="settings-summary-row">
              <span>Current equity</span>
              <span>{fmt$(account?.equity)}</span>
            </div>
            <div className="settings-summary-row">
              <span>Base capital</span>
              <span>{fmt$(parseFloat(values.BASE_CAPITAL))}</span>
            </div>
            <div className={`settings-summary-row ${withdrawable > 0 ? 'green' : ''}`}>
              <span>Withdrawable profit</span>
              <strong>{fmt$(withdrawable)}</strong>
            </div>
            <div className="settings-summary-row">
              <span>Alert fires at</span>
              <span>{fmt$(alertAt)} profit</span>
            </div>
          </div>

          <button
            className="settings-save-btn"
            onClick={handleSave}
            disabled={saving}
          >
            {saving ? 'Saving…' : 'Save Settings'}
          </button>
        </div>
      ) : (
        !error && <p className="settings-loading">Loading…</p>
      )}
    </div>
  )
}

// ------------------------------------------------------------------ Reports Tab

const REPORT_PERIODS = [
  { label: '7 days',  days: 7  },
  { label: '30 days', days: 30 },
  { label: '90 days', days: 90 },
]

function ReportCard({ title, description, onDownload, loading }) {
  return (
    <div className="report-card">
      <div className="report-card-body">
        <div className="report-card-title">{title}</div>
        <div className="report-card-desc">{description}</div>
      </div>
      <button
        className="btn btn-primary report-dl-btn"
        onClick={onDownload}
        disabled={loading}
      >
        {loading ? 'Generating…' : '↓ Download PDF'}
      </button>
    </div>
  )
}

function ReportsTab({ data }) {
  const [period, setPeriod] = useState(30)
  const [loading, setLoading] = useState({})

  const setLoad = (key, val) => setLoading(l => ({ ...l, [key]: val }))

  const buildPdf = async (key, generate) => {
    setLoad(key, true)
    try {
      await generate()
    } catch (err) {
      alert('PDF generation failed: ' + (err.message || err))
    } finally {
      setLoad(key, false)
    }
  }

  // ---- Performance Summary PDF ----
  const downloadPerformance = () => buildPdf('perf', async () => {
    const { jsPDF } = await import('jspdf')
    const { default: autoTable } = await import('jspdf-autotable')

    const analyticsRes = await fetchAnalytics().catch(() => ({ data: null }))
    const reviewRes    = await fetchReview(period).catch(() => ({ data: null }))
    const analytics    = analyticsRes.data
    const review       = reviewRes.data

    const doc = new jsPDF({ orientation: 'portrait', unit: 'mm', format: 'a4' })
    const W = doc.internal.pageSize.getWidth()
    const now = new Date().toLocaleString('en-US', { dateStyle: 'long', timeStyle: 'short' })

    // Header bar
    doc.setFillColor(13, 17, 23)
    doc.rect(0, 0, W, 22, 'F')
    doc.setTextColor(255, 255, 255)
    doc.setFontSize(16)
    doc.setFont('helvetica', 'bold')
    doc.text('STOX', 14, 14)
    doc.setFontSize(10)
    doc.setFont('helvetica', 'normal')
    doc.text('Performance Summary Report', 35, 14)
    doc.setTextColor(139, 148, 158)
    doc.setFontSize(9)
    doc.text(`Generated: ${now}  ·  Period: last ${period} days`, W - 14, 14, { align: 'right' })

    // Account metrics
    const { account, summary } = data
    const pnl = summary?.total_pnl ?? 0
    const winRate = summary?.win_rate ?? 0

    doc.setTextColor(30, 30, 30)
    doc.setFontSize(13)
    doc.setFont('helvetica', 'bold')
    doc.text('Account Overview', 14, 34)

    autoTable(doc, {
      startY: 38,
      head: [['Metric', 'Value']],
      body: [
        ['Portfolio Equity',   account?.equity != null ? `$${Number(account.equity).toLocaleString('en-US', { minimumFractionDigits: 2 })}` : '—'],
        ['Cash Balance',       account?.cash   != null ? `$${Number(account.cash).toLocaleString('en-US',   { minimumFractionDigits: 2 })}` : '—'],
        ['Buying Power',       account?.buying_power != null ? `$${Number(account.buying_power).toLocaleString('en-US', { minimumFractionDigits: 2 })}` : '—'],
        ['Realised P&L',       `$${pnl.toLocaleString('en-US', { minimumFractionDigits: 2 })}  (${pnl >= 0 ? '+' : ''}${((pnl / (account?.equity - pnl || 1)) * 100).toFixed(2)}%)`],
        ['Closed Trades',      String(summary?.total_trades ?? 0)],
        ['Win Rate',           `${(winRate * 100).toFixed(1)}%`],
        ['Profit Factor',      summary?.profit_factor === Infinity ? '∞' : (summary?.profit_factor ?? 0).toFixed(2) + 'x'],
      ],
      theme: 'striped',
      headStyles: { fillColor: [13, 17, 23], textColor: 255, fontStyle: 'bold' },
      columnStyles: { 0: { fontStyle: 'bold', cellWidth: 70 } },
      margin: { left: 14, right: 14 },
    })

    // Risk metrics
    if (analytics) {
      const y = doc.lastAutoTable.finalY + 10
      doc.setFontSize(13)
      doc.setFont('helvetica', 'bold')
      doc.text('Risk Metrics', 14, y)

      autoTable(doc, {
        startY: y + 4,
        head: [['Metric', 'Value', 'Benchmark']],
        body: [
          ['Sharpe Ratio',      analytics.sharpe   ?? '—', '≥ 1.0 (good)'],
          ['Sortino Ratio',     analytics.sortino  ?? '—', '≥ 1.0 (good)'],
          ['Calmar Ratio',      analytics.calmar   ?? '—', '≥ 0.5 (acceptable)'],
          ['Max Drawdown',      analytics.max_drawdown_pct != null ? `${analytics.max_drawdown_pct}%` : '—', '< 20% (target)'],
          ['VaR (95%, 1-day)',  analytics.var_95_pct != null ? `${analytics.var_95_pct}%` : '—', '< 2% (target)'],
          ['Total Return',      analytics.total_return_pct != null ? `${analytics.total_return_pct}%` : '—', '> 0%'],
        ],
        theme: 'striped',
        headStyles: { fillColor: [13, 17, 23], textColor: 255, fontStyle: 'bold' },
        columnStyles: { 0: { fontStyle: 'bold', cellWidth: 70 }, 2: { textColor: [100, 100, 100] } },
        margin: { left: 14, right: 14 },
      })
    }

    // Strategy review / top performers
    const stats = review?.stats?.recent || review?.stats?.all_time
    if (stats && stats.count > 0) {
      const y2 = doc.lastAutoTable.finalY + 10
      doc.setFontSize(13)
      doc.setFont('helvetica', 'bold')
      doc.text(`Strategy Review (last ${period} days)`, 14, y2)

      const symStats = review.symbol_stats || {}
      const symbols  = Object.entries(symStats).sort((a, b) => (b[1].total_pnl || 0) - (a[1].total_pnl || 0))
      const topRows  = symbols.slice(0, 10).map(([sym, s]) => [
        sym,
        String(s.count ?? 0),
        `${((s.win_rate || 0) * 100).toFixed(0)}%`,
        `$${(s.total_pnl || 0).toFixed(2)}`,
        `${((s.avg_pnl_pct || 0) * 100).toFixed(2)}%`,
      ])

      if (topRows.length > 0) {
        autoTable(doc, {
          startY: y2 + 4,
          head: [['Symbol', 'Trades', 'Win Rate', 'Total P&L', 'Avg P&L %']],
          body: topRows,
          theme: 'striped',
          headStyles: { fillColor: [13, 17, 23], textColor: 255, fontStyle: 'bold' },
          margin: { left: 14, right: 14 },
        })
      }
    }

    // Footer
    const pages = doc.internal.getNumberOfPages()
    for (let i = 1; i <= pages; i++) {
      doc.setPage(i)
      doc.setFontSize(8)
      doc.setTextColor(139, 148, 158)
      doc.text('STOX Algorithmic Trading — Confidential', 14, 290)
      doc.text(`Page ${i} of ${pages}`, W - 14, 290, { align: 'right' })
    }

    doc.save(`stox-performance-${new Date().toISOString().slice(0, 10)}.pdf`)
  })

  // ---- Trade History PDF ----
  const downloadTradeHistory = () => buildPdf('trades', async () => {
    const { jsPDF } = await import('jspdf')
    const { default: autoTable } = await import('jspdf-autotable')

    const doc = new jsPDF({ orientation: 'landscape', unit: 'mm', format: 'a4' })
    const W   = doc.internal.pageSize.getWidth()
    const now = new Date().toLocaleString('en-US', { dateStyle: 'long', timeStyle: 'short' })

    // Header
    doc.setFillColor(13, 17, 23)
    doc.rect(0, 0, W, 22, 'F')
    doc.setTextColor(255, 255, 255)
    doc.setFontSize(16)
    doc.setFont('helvetica', 'bold')
    doc.text('STOX', 14, 14)
    doc.setFontSize(10)
    doc.setFont('helvetica', 'normal')
    doc.text('Trade History Report', 35, 14)
    doc.setTextColor(139, 148, 158)
    doc.setFontSize(9)
    doc.text(`Generated: ${now}`, W - 14, 14, { align: 'right' })

    const { trades, summary } = data

    // Summary row
    doc.setTextColor(30, 30, 30)
    doc.setFontSize(9)
    doc.setFont('helvetica', 'normal')
    doc.text(
      `Total trades: ${summary?.total_trades ?? 0}  ·  Win rate: ${((summary?.win_rate ?? 0) * 100).toFixed(1)}%  ·  Realised P&L: $${(summary?.total_pnl ?? 0).toFixed(2)}  ·  Profit factor: ${summary?.profit_factor === Infinity ? '∞' : (summary?.profit_factor ?? 0).toFixed(2)}x`,
      14, 30
    )

    const closed = trades.filter(t => t.status !== 'OPEN')
    const rows   = closed.map(t => [
      t.symbol,
      String(t.shares ?? ''),
      t.entry_price != null ? `$${Number(t.entry_price).toFixed(2)}` : '—',
      t.exit_price  != null ? `$${Number(t.exit_price).toFixed(2)}`  : '—',
      t.pnl != null ? `$${Number(t.pnl).toFixed(2)}` : '—',
      t.pnl_pct != null ? `${(t.pnl_pct * 100).toFixed(2)}%` : '—',
      t.status,
      t.opened_at ? new Date(t.opened_at).toLocaleDateString('en-US') : '—',
      t.closed_at ? new Date(t.closed_at).toLocaleDateString('en-US') : '—',
    ])

    autoTable(doc, {
      startY: 34,
      head: [['Symbol', 'Shares', 'Entry', 'Exit', 'P&L ($)', 'P&L %', 'Status', 'Opened', 'Closed']],
      body: rows.length > 0 ? rows : [['No closed trades in history', '', '', '', '', '', '', '', '']],
      theme: 'striped',
      headStyles: { fillColor: [13, 17, 23], textColor: 255, fontStyle: 'bold', fontSize: 8 },
      bodyStyles: { fontSize: 8 },
      columnStyles: {
        4: { halign: 'right' },
        5: { halign: 'right' },
      },
      didParseCell: (hookData) => {
        if (hookData.section === 'body' && hookData.column.index === 4) {
          const val = hookData.cell.raw
          if (typeof val === 'string' && val.startsWith('$-')) {
            hookData.cell.styles.textColor = [218, 54, 51]
          } else if (typeof val === 'string' && val.startsWith('$') && val !== '$—') {
            hookData.cell.styles.textColor = [35, 134, 54]
          }
        }
      },
      margin: { left: 14, right: 14 },
    })

    // Footer
    const pages = doc.internal.getNumberOfPages()
    for (let i = 1; i <= pages; i++) {
      doc.setPage(i)
      doc.setFontSize(8)
      doc.setTextColor(139, 148, 158)
      doc.text('STOX Algorithmic Trading — Confidential', 14, doc.internal.pageSize.getHeight() - 7)
      doc.text(`Page ${i} of ${pages}`, W - 14, doc.internal.pageSize.getHeight() - 7, { align: 'right' })
    }

    doc.save(`stox-trades-${new Date().toISOString().slice(0, 10)}.pdf`)
  })

  // ---- Strategy Review PDF ----
  const downloadStrategyReview = () => buildPdf('review', async () => {
    const { jsPDF } = await import('jspdf')
    const { default: autoTable } = await import('jspdf-autotable')

    const reviewRes = await fetchReview(period)
    const review    = reviewRes.data
    const stats     = review?.stats || {}
    const recs      = review?.recommendations || []
    const recent    = stats.recent || {}
    const allTime   = stats.all_time || {}

    const doc = new jsPDF({ orientation: 'portrait', unit: 'mm', format: 'a4' })
    const W   = doc.internal.pageSize.getWidth()
    const now = new Date().toLocaleString('en-US', { dateStyle: 'long', timeStyle: 'short' })

    // Header
    doc.setFillColor(13, 17, 23)
    doc.rect(0, 0, W, 22, 'F')
    doc.setTextColor(255, 255, 255)
    doc.setFontSize(16)
    doc.setFont('helvetica', 'bold')
    doc.text('STOX', 14, 14)
    doc.setFontSize(10)
    doc.setFont('helvetica', 'normal')
    doc.text('Strategy Review Report', 35, 14)
    doc.setTextColor(139, 148, 158)
    doc.setFontSize(9)
    doc.text(`Generated: ${now}  ·  Period: last ${period} days`, W - 14, 14, { align: 'right' })

    // Recent vs all-time stats
    doc.setTextColor(30, 30, 30)
    doc.setFontSize(13)
    doc.setFont('helvetica', 'bold')
    doc.text('Performance Statistics', 14, 34)

    autoTable(doc, {
      startY: 38,
      head: [['Metric', `Last ${period} days`, 'All Time']],
      body: [
        ['Trades',        String(recent.count ?? 0),       String(allTime.count ?? 0)],
        ['Win Rate',      `${((recent.win_rate ?? 0) * 100).toFixed(1)}%`,    `${((allTime.win_rate ?? 0) * 100).toFixed(1)}%`],
        ['Profit Factor', recent.profit_factor === Infinity ? '∞' : (recent.profit_factor ?? 0).toFixed(2) + 'x', allTime.profit_factor === Infinity ? '∞' : (allTime.profit_factor ?? 0).toFixed(2) + 'x'],
        ['Total P&L',     `$${(recent.total_pnl ?? 0).toFixed(2)}`,  `$${(allTime.total_pnl ?? 0).toFixed(2)}`],
        ['Avg P&L/trade', `$${(recent.avg_pnl ?? 0).toFixed(2)}`,    `$${(allTime.avg_pnl ?? 0).toFixed(2)}`],
        ['Avg P&L %',     `${((recent.avg_pnl_pct ?? 0) * 100).toFixed(2)}%`, `${((allTime.avg_pnl_pct ?? 0) * 100).toFixed(2)}%`],
        ['Biggest Win',   `$${(recent.biggest_win ?? 0).toFixed(2)}`,  `$${(allTime.biggest_win ?? 0).toFixed(2)}`],
        ['Biggest Loss',  `$${(recent.biggest_loss ?? 0).toFixed(2)}`, `$${(allTime.biggest_loss ?? 0).toFixed(2)}`],
      ],
      theme: 'striped',
      headStyles: { fillColor: [13, 17, 23], textColor: 255, fontStyle: 'bold' },
      columnStyles: { 0: { fontStyle: 'bold', cellWidth: 60 } },
      margin: { left: 14, right: 14 },
    })

    // Per-symbol stats
    const symStats = review?.symbol_stats || {}
    const symbols  = Object.entries(symStats).sort((a, b) => (b[1].total_pnl || 0) - (a[1].total_pnl || 0))
    if (symbols.length > 0) {
      const y = doc.lastAutoTable.finalY + 10
      doc.setFontSize(13)
      doc.setFont('helvetica', 'bold')
      doc.text('Per-Symbol Breakdown', 14, y)

      autoTable(doc, {
        startY: y + 4,
        head: [['Symbol', 'Trades', 'Wins', 'Win Rate', 'Total P&L', 'Avg P&L %', 'Best', 'Worst']],
        body: symbols.map(([sym, s]) => [
          sym,
          String(s.count ?? 0),
          String(s.wins ?? 0),
          `${((s.win_rate || 0) * 100).toFixed(0)}%`,
          `$${(s.total_pnl || 0).toFixed(2)}`,
          `${((s.avg_pnl_pct || 0) * 100).toFixed(2)}%`,
          `$${(s.biggest_win || 0).toFixed(2)}`,
          `$${(s.biggest_loss || 0).toFixed(2)}`,
        ]),
        theme: 'striped',
        headStyles: { fillColor: [13, 17, 23], textColor: 255, fontStyle: 'bold', fontSize: 8 },
        bodyStyles: { fontSize: 8 },
        margin: { left: 14, right: 14 },
      })
    }

    // Recommendations
    if (recs.length > 0) {
      const y3 = doc.lastAutoTable.finalY + 10
      doc.setFontSize(13)
      doc.setFont('helvetica', 'bold')
      doc.text('Parameter Recommendations', 14, y3)

      autoTable(doc, {
        startY: y3 + 4,
        head: [['Priority', 'Parameter', 'Current', 'Recommended', 'Reason']],
        body: recs.map(r => [
          (r.priority || '').toUpperCase(),
          r.parameter || '—',
          String(r.current ?? '—'),
          String(r.recommended ?? r.action ?? '—'),
          r.reason || '—',
        ]),
        theme: 'striped',
        headStyles: { fillColor: [13, 17, 23], textColor: 255, fontStyle: 'bold', fontSize: 8 },
        bodyStyles: { fontSize: 8 },
        columnStyles: { 4: { cellWidth: 70 } },
        didParseCell: (hookData) => {
          if (hookData.section === 'body' && hookData.column.index === 0) {
            const val = hookData.cell.raw
            if (val === 'HIGH')   hookData.cell.styles.textColor = [218, 54, 51]
            if (val === 'MEDIUM') hookData.cell.styles.textColor = [210, 153, 34]
          }
        },
        margin: { left: 14, right: 14 },
      })
    }

    // Footer
    const pages = doc.internal.getNumberOfPages()
    for (let i = 1; i <= pages; i++) {
      doc.setPage(i)
      doc.setFontSize(8)
      doc.setTextColor(139, 148, 158)
      doc.text('STOX Algorithmic Trading — Confidential', 14, 290)
      doc.text(`Page ${i} of ${pages}`, W - 14, 290, { align: 'right' })
    }

    doc.save(`stox-strategy-review-${new Date().toISOString().slice(0, 10)}.pdf`)
  })

  // ---- Quant Benchmarking PDF ----
  const downloadQuantReport = () => buildPdf('quant', async () => {
    const { jsPDF } = await import('jspdf')
    const { default: autoTable } = await import('jspdf-autotable')

    const doc = new jsPDF({ orientation: 'portrait', unit: 'mm', format: 'a4' })
    const W = doc.internal.pageSize.getWidth()
    const now = new Date().toLocaleString('en-US', { dateStyle: 'long', timeStyle: 'short' })

    const addPage = () => { doc.addPage(); return 28 }

    const sectionTitle = (label, y) => {
      doc.setFontSize(11)
      doc.setFont('helvetica', 'bold')
      doc.setTextColor(30, 30, 30)
      doc.text(label, 14, y)
      doc.setDrawColor(48, 54, 61)
      doc.setLineWidth(0.3)
      doc.line(14, y + 1.5, W - 14, y + 1.5)
      return y + 7
    }

    const bodyText = (lines, y, maxW = W - 28) => {
      doc.setFontSize(9)
      doc.setFont('helvetica', 'normal')
      doc.setTextColor(50, 50, 50)
      for (const line of lines) {
        const wrapped = doc.splitTextToSize(line, maxW)
        doc.text(wrapped, 14, y)
        y += wrapped.length * 4.5
        if (y > 268) y = addPage()
      }
      return y + 2
    }

    // ── Header ──────────────────────────────────────────────────────────────
    doc.setFillColor(13, 17, 23)
    doc.rect(0, 0, W, 22, 'F')
    doc.setTextColor(255, 255, 255)
    doc.setFontSize(16)
    doc.setFont('helvetica', 'bold')
    doc.text('STOX', 14, 14)
    doc.setFontSize(10)
    doc.setFont('helvetica', 'normal')
    doc.text('Quant Benchmarking Report', 35, 14)
    doc.setTextColor(139, 148, 158)
    doc.setFontSize(9)
    doc.text(`Generated: ${now}`, W - 14, 14, { align: 'right' })

    // ── Overall grade banner ─────────────────────────────────────────────────
    doc.setFillColor(22, 27, 34)
    doc.rect(14, 26, W - 28, 16, 'F')
    doc.setTextColor(88, 166, 255)
    doc.setFontSize(13)
    doc.setFont('helvetica', 'bold')
    doc.text('Overall Grade: C+  (2.8 / 4.0)', 20, 35)
    doc.setFontSize(8)
    doc.setFont('helvetica', 'normal')
    doc.setTextColor(139, 148, 158)
    doc.text('Retail-grade. Solid foundation. Significant gaps before competing with institutional capital.', 20, 40)

    let y = 50

    // ── System scorecard ────────────────────────────────────────────────────
    y = sectionTitle('System Scorecard', y)
    autoTable(doc, {
      startY: y,
      head: [['Dimension', 'Score', 'Notes']],
      body: [
        ['Signal Clarity',          '8 / 10', 'Clear EMA/RSI/MACD logic; no divergences'],
        ['Risk Management',         '6 / 10', 'Kelly sizing good; no stress testing'],
        ['Data Quality',            '7 / 10', 'yfinance reliable; no validation layer'],
        ['Execution Latency',       '5 / 10', '10-min scans; no intraday signals'],
        ['Order Execution',         '4 / 10', 'Market orders; slippage blind spots'],
        ['Diversification',         '7 / 10', '20-position limit + correlation check'],
        ['Sentiment Analysis',      '7 / 10', '4-source model good; implementation gaps'],
        ['Regime Awareness',        '7 / 10', '4-regime model; VIX threshold coarse'],
        ['ML Integration',          '5 / 10', 'RF classifier useful; no validation rigor'],
        ['Pairs / Stat-Arb',        '4 / 10', 'Hardcoded pairs; static hedge ratio'],
        ['Institutional Readiness', '4 / 10', 'No compliance/audit trail'],
      ],
      theme: 'striped',
      headStyles: { fillColor: [13, 17, 23], textColor: 255, fontStyle: 'bold', fontSize: 8 },
      bodyStyles: { fontSize: 8 },
      columnStyles: { 0: { fontStyle: 'bold', cellWidth: 60 }, 1: { cellWidth: 20, halign: 'center' } },
      didParseCell: (h) => {
        if (h.section === 'body' && h.column.index === 1) {
          const v = parseFloat(h.cell.raw)
          if (v >= 7) h.cell.styles.textColor = [35, 134, 54]
          else if (v <= 4) h.cell.styles.textColor = [218, 54, 51]
          else h.cell.styles.textColor = [210, 153, 34]
        }
      },
      margin: { left: 14, right: 14 },
    })
    y = doc.lastAutoTable.finalY + 10
    if (y > 240) y = addPage()

    // ── What you have ────────────────────────────────────────────────────────
    y = sectionTitle('What STOX Has Built (and It\'s More Than Most Retail Bots)', y)
    autoTable(doc, {
      startY: y,
      head: [['Layer', 'Implementation', 'Quality']],
      body: [
        ['Signal engine',       'EMA9/21/50 + RSI14 + MACD + BB, 0–100 scored',               'Good'],
        ['Multi-timeframe',     'Weekly chart confirmation filter',                             'Good'],
        ['Regime detection',    '4-regime (BULL/RANGING/BEAR/HIGH_VOL) + ADX + VIX',           'Good'],
        ['Sentiment',           '4-source composite (options, analyst, insider, retail)',       'Above average'],
        ['ML gate',             'RandomForest classifier, 52% confidence threshold',            'Functional'],
        ['13F smart money',     '8 hedge funds tracked via SEC EDGAR',                         'Unique for retail'],
        ['Pairs / stat-arb',    '13 cointegrated pairs, z-score entry/exit',                   'Rare at this level'],
        ['Short selling',       'Sector + sentiment + weekly confirmation required',            'Solid'],
        ['Kelly sizing',        'Half-Kelly with 20-trade warmup',                              'Correct approach'],
        ['Trailing stops',      '4%/5%/7% tiered by gain + break-even protection',             'Strong'],
        ['Risk metrics',        'Sharpe, Sortino, Calmar, VaR, Max DD',                        'Institutional-grade'],
      ],
      theme: 'striped',
      headStyles: { fillColor: [13, 17, 23], textColor: 255, fontStyle: 'bold', fontSize: 8 },
      bodyStyles: { fontSize: 8 },
      columnStyles: { 0: { fontStyle: 'bold', cellWidth: 45 }, 2: { cellWidth: 30 } },
      didParseCell: (h) => {
        if (h.section === 'body' && h.column.index === 2) {
          const v = h.cell.raw
          if (v === 'Good' || v === 'Strong' || v === 'Correct approach') h.cell.styles.textColor = [35, 134, 54]
          else if (v === 'Above average' || v === 'Solid' || v === 'Institutional-grade') h.cell.styles.textColor = [88, 166, 255]
          else if (v === 'Unique for retail' || v === 'Rare at this level') h.cell.styles.textColor = [188, 140, 255]
        }
      },
      margin: { left: 14, right: 14 },
    })
    y = doc.lastAutoTable.finalY + 10
    if (y > 240) y = addPage()

    // ── Performance benchmarks ───────────────────────────────────────────────
    y = sectionTitle('Performance Expectations vs. Institutional Benchmarks', y)
    autoTable(doc, {
      startY: y,
      head: [['Metric', 'STOX (Estimated)', 'Institutional Quant', 'Gap']],
      body: [
        ['Sharpe Ratio',           '0.8 – 1.2',    '2.0 – 3.5',    '~60-70% lower'],
        ['Max Drawdown',           '15 – 25%',     '< 10%',         '1.5 – 2.5× worse'],
        ['Win Rate',               '50 – 55%',     '55 – 70%',      'Moderate'],
        ['Annual Turnover',        '4 – 8×',        '10 – 50×',      'Much lower'],
        ['Slippage modeled',       'None',          'Every tick',    'Blind spot'],
        ['Scan frequency',         '10 minutes',   'Tick / <1ms',   'Glacial by comparison'],
      ],
      theme: 'striped',
      headStyles: { fillColor: [13, 17, 23], textColor: 255, fontStyle: 'bold', fontSize: 8 },
      bodyStyles: { fontSize: 8 },
      columnStyles: { 0: { fontStyle: 'bold', cellWidth: 50 } },
      didParseCell: (h) => {
        if (h.section === 'body' && h.column.index === 3 && h.cell.raw !== 'Moderate') {
          h.cell.styles.textColor = [218, 54, 51]
        }
      },
      margin: { left: 14, right: 14 },
    })
    y = doc.lastAutoTable.finalY + 10
    if (y > 240) y = addPage()

    // ── Top 5 improvements ───────────────────────────────────────────────────
    y = sectionTitle('Top 5 Improvements by Impact', y)
    autoTable(doc, {
      startY: y,
      head: [['Priority', 'Change', 'Why']],
      body: [
        ['1 — HIGH',   'Partial exits (sell 33% at +8%, 33% at +15%, trail rest)',          'Locks in realised gains before reversals; reduces variance'],
        ['2 — HIGH',   'Walk-forward backtest with 0.1% slippage + commission',             'Makes performance figures honest; current backtest overstates returns by 20-40%'],
        ['3 — MEDIUM', 'Dynamic pairs cointegration (re-test pairs weekly)',                'Hardcoded pairs decointegrate over time; stale pairs create false signals'],
        ['4 — MEDIUM', 'Factor exposure monitoring (daily beta + sector exposure)',          'Prevents hidden concentration; 15 Nasdaq-heavy longs = correlated crash risk'],
        ['5 — MEDIUM', 'Retrain ML monthly on actual closed trade outcomes',                'Model improves with real data; currently trains only on historical price patterns'],
      ],
      theme: 'striped',
      headStyles: { fillColor: [13, 17, 23], textColor: 255, fontStyle: 'bold', fontSize: 8 },
      bodyStyles: { fontSize: 8 },
      columnStyles: { 0: { cellWidth: 28, fontStyle: 'bold' }, 1: { cellWidth: 65 } },
      didParseCell: (h) => {
        if (h.section === 'body' && h.column.index === 0) {
          if (h.cell.raw.startsWith('1') || h.cell.raw.startsWith('2')) h.cell.styles.textColor = [218, 54, 51]
          else h.cell.styles.textColor = [210, 153, 34]
        }
      },
      margin: { left: 14, right: 14 },
    })
    y = doc.lastAutoTable.finalY + 10
    if (y > 240) y = addPage()

    // ── Where you're behind ──────────────────────────────────────────────────
    y = sectionTitle('Key Institutional Gaps', y)
    const gaps = [
      ['Execution', 'Market orders with no slippage modeling. On mid-cap names you move the market against yourself. Institutional desks use VWAP/TWAP algorithms and dark pool routing.'],
      ['ML Pipeline', 'RandomForest depth=5 with no time-series cross-validation. Renaissance runs thousands of signals through ensemble models with walk-forward validation and live execution feedback.'],
      ['Pairs Trading', '13 hardcoded pairs with static OLS hedge ratio. Institutional stat-arb desks test thousands of pairs dynamically, use Kalman-filtered hedge ratios, and model transaction costs per entry.'],
      ['Sentiment Data', 'StockTwits lags price by hours. Real institutional edge comes from satellite imagery, credit card transactions, patent filings, and NLP on SEC filings.'],
      ['13F Filings', '45-day-old positioning data. Funds track Form 4 insider filings in real-time and prime brokerage short interest with <24h latency.'],
      ['Portfolio Risk', 'Correlation checked at entry only. No ongoing factor exposure monitoring (beta, momentum factor, size factor) or tail-risk hedging via OTM puts.'],
    ]
    for (const [title, desc] of gaps) {
      if (y > 260) y = addPage()
      doc.setFontSize(9)
      doc.setFont('helvetica', 'bold')
      doc.setTextColor(218, 54, 51)
      doc.text(`▸ ${title}`, 14, y)
      y += 4.5
      doc.setFont('helvetica', 'normal')
      doc.setTextColor(60, 60, 60)
      const wrapped = doc.splitTextToSize(desc, W - 28)
      doc.text(wrapped, 14, y)
      y += wrapped.length * 4.5 + 3
    }

    // ── Where you punch above weight ─────────────────────────────────────────
    y += 2
    if (y > 240) y = addPage()
    y = sectionTitle('Where STOX Punches Above Its Weight', y)
    const strengths = [
      'Regime-conditional sizing (0× in HIGH_VOL, 0.5× in BEAR) — most retail bots ignore market regimes entirely.',
      'Break-even stop (never let a +3% winner become a loser) — a sound professional technique rarely seen at retail.',
      'Earnings blackout prevents the most common retail blow-up: holding through an earnings print unknowingly.',
      'Weekly chart confirmation filter is a strong false-positive reducer. Most retail bots fire on daily noise.',
      '4-source sentiment composite and 13F smart money tracking are institutional-style features rare at this capital size.',
    ]
    y = bodyText(strengths.map(s => `• ${s}`), y)

    // ── Bottom line ───────────────────────────────────────────────────────────
    y += 2
    if (y > 240) y = addPage()
    y = sectionTitle('Bottom Line', y)
    y = bodyText([
      'STOX is in the top 5% of retail algorithmic systems. Multi-regime awareness, institutional-style risk metrics, sentiment composite, and pairs trading put it well ahead of the typical EMA-crossover bot.',
      'It is still fundamentally a retail system: market orders, 10-minute latency, static ML, and no slippage modeling mean the edge it thinks it has on paper is partially an artefact of optimistic backtesting.',
      'To compete with a billion-dollar quant fund you would need co-located execution infrastructure, tick data, alternative data feeds, a proper ML research pipeline, and a compliance/audit layer — a $10M+ technology investment.',
      'For a paper-trading bot growing toward live deployment, STOX is well-architected and improvable. Keep ambitions proportional to capital size, focus on the top-5 improvements, and the strategy is sound.',
    ], y)

    // ── Footer ────────────────────────────────────────────────────────────────
    const pages = doc.internal.getNumberOfPages()
    for (let i = 1; i <= pages; i++) {
      doc.setPage(i)
      doc.setFontSize(8)
      doc.setTextColor(139, 148, 158)
      doc.text('STOX Algorithmic Trading — Confidential', 14, 290)
      doc.text(`Page ${i} of ${pages}`, W - 14, 290, { align: 'right' })
    }

    doc.save(`stox-quant-benchmarking-${new Date().toISOString().slice(0, 10)}.pdf`)
  })

  // ---- Platform Overview PDF ----
  const downloadPlatformOverview = () => buildPdf('overview', async () => {
    const { jsPDF } = await import('jspdf')
    const { default: autoTable } = await import('jspdf-autotable')

    const doc = new jsPDF({ orientation: 'portrait', unit: 'mm', format: 'a4' })
    const W = doc.internal.pageSize.getWidth()
    const now = new Date().toLocaleString('en-US', { dateStyle: 'long', timeStyle: 'short' })

    const addHeader = (subtitle) => {
      doc.setFillColor(13, 17, 23)
      doc.rect(0, 0, W, 22, 'F')
      doc.setTextColor(255, 255, 255)
      doc.setFontSize(16)
      doc.setFont('helvetica', 'bold')
      doc.text('STOX', 14, 14)
      doc.setFontSize(10)
      doc.setFont('helvetica', 'normal')
      doc.text(subtitle, 35, 14)
      doc.setTextColor(139, 148, 158)
      doc.setFontSize(9)
      doc.text(`Generated: ${now}`, W - 14, 14, { align: 'right' })
    }

    const addSection = (title, y) => {
      doc.setTextColor(13, 17, 23)
      doc.setFontSize(11)
      doc.setFont('helvetica', 'bold')
      doc.text(title, 14, y)
      doc.setDrawColor(200, 200, 200)
      doc.line(14, y + 2, W - 14, y + 2)
      return y + 8
    }

    const TH = { fillColor: [13, 17, 23], textColor: 255, fontStyle: 'bold', fontSize: 9 }
    const TB = { fontSize: 9 }
    const M  = { left: 14, right: 14 }
    const nextY = () => doc.lastAutoTable.finalY + 10

    // ── Page 1 ──────────────────────────────────────────────────────
    addHeader('Platform Overview')
    let y = 32

    doc.setTextColor(50, 50, 50)
    doc.setFontSize(9)
    doc.setFont('helvetica', 'normal')
    const intro = 'STOX is an algorithmic trading bot running on Railway, trading US equities via Alpaca\'s paper trading API. It runs a continuous scan loop during market hours, filtering buy candidates through 8 layers of checks before placing any order. A React dashboard gives live visibility into positions, trades, and risk metrics.'
    const introLines = doc.splitTextToSize(intro, W - 28)
    doc.text(introLines, 14, y)
    y += introLines.length * 5 + 6

    y = addSection('Infrastructure', y)
    autoTable(doc, { startY: y, head: [['Component', 'Details']], body: [
      ['Trading Bot',     'Railway (always-on cloud process)'],
      ['REST API',        'FastAPI, same Railway container'],
      ['Dashboard',       'React SPA, served from Railway'],
      ['Data Persistence','logs/portfolio.json on Railway disk'],
      ['Market Data',     'yfinance + Alpaca'],
      ['Brokerage',       'Alpaca Paper Trading API'],
      ['Auto-Deploy',     'Push to claude/stock-trading-bot-QeUGQ → Railway redeploys'],
    ], theme: 'striped', headStyles: TH, bodyStyles: TB, columnStyles: { 0: { fontStyle: 'bold', cellWidth: 52 } }, margin: M })

    y = addSection('Capital Management', nextY())
    autoTable(doc, { startY: y, head: [['Concept', 'Description']], body: [
      ['BASE_CAPITAL ($100,000)', 'Authorised trading ceiling. Profits above this never get reinvested.'],
      ['Idle Cash (Above Base)',  'Actual withdrawable surplus shown in the dashboard stat card.'],
      ['Unrealised Growth',       'Paper gains on open positions — not withdrawable until closed.'],
      ['Cash Calculation',        'min(buying_power, BASE_CAPITAL) — avoids T+2 settlement lag going negative.'],
    ], theme: 'striped', headStyles: TH, bodyStyles: TB, columnStyles: { 0: { fontStyle: 'bold', cellWidth: 62 } }, margin: M })

    y = addSection('Risk Controls', nextY())
    autoTable(doc, { startY: y, head: [['Control', 'Value']], body: [
      ['Max position size',           '5% of equity'],
      ['Max loss per trade',           '3% stop loss'],
      ['Daily loss circuit-breaker',  '5% of equity — halts all new entries for the day'],
      ['Max positions per sector',    '3'],
      ['Correlation limit',            'Pearson r > 0.70 — skips correlated entries'],
      ['VIX block',                   'VIX > 30 — no new longs'],
      ['Open 15-min blackout',        '9:30–9:45 ET — skip volatile open window'],
      ['Earnings blackout',           '2 days before / after earnings date'],
      ['IPO quarantine',              '5 days before trading new IPOs'],
      ['Break-even protection',       'Once up 5%, never close below entry price'],
    ], theme: 'striped', headStyles: TH, bodyStyles: TB, columnStyles: { 0: { fontStyle: 'bold', cellWidth: 72 } }, margin: M })

    // ── Page 2: Scan Loop ────────────────────────────────────────────
    doc.addPage()
    addHeader('Platform Overview — Scan Loop')
    y = 32

    y = addSection('Step 1 — Exit Management  (always runs first, every scan)', y)
    autoTable(doc, { startY: y, head: [['Trigger', 'Condition']], body: [
      ['Hard stop loss',    'Price drops 3% below entry'],
      ['Break-even stop',   'Once up 5%, never close below entry'],
      ['Trailing stop',     '4% below peak  (widens to 5% at +8% gain,  7% at +18%)'],
      ['Partial exit',      'Sell 33% at +8%,  another 33% at +15%,  trail the rest'],
      ['Take profit',       'Emergency ceiling at +30% (trailing stop exits first in practice)'],
      ['Signal exit',       'EMA death cross or MACD histogram flips negative'],
    ], theme: 'striped', headStyles: TH, bodyStyles: TB, columnStyles: { 0: { fontStyle: 'bold', cellWidth: 48 } }, margin: M })

    y = addSection('Step 2 — Entry Gate Checks  (global blockers)', nextY())
    autoTable(doc, { startY: y, head: [['Check', 'Rule']], body: [
      ['Daily loss circuit-breaker', 'Halt if today\'s loss ≥ 5% of portfolio'],
      ['VIX filter',                 'Block all longs if VIX > 30'],
      ['Market regime',              'BULL 1.0×  |  RANGING 0.6×  |  BEAR 0.5×  |  HIGH_VOL block'],
      ['Buying power',               'Skip if available cash < 1% of equity'],
    ], theme: 'striped', headStyles: TH, bodyStyles: TB, columnStyles: { 0: { fontStyle: 'bold', cellWidth: 62 } }, margin: M })

    y = addSection('Steps 3–4 — Universe Assembly & Signal Generation', nextY())
    autoTable(doc, { startY: y, head: [['Source', 'Detail']], body: [
      ['Watchlist (40 symbols)',    'Large-cap US: AAPL, MSFT, NVDA, XOM, JPM, GOOGL, META…'],
      ['Dynamic breakouts (10)',   'Top momentum screeners from a 140-symbol extended universe'],
      ['News-discovered symbols',  'Alpaca news feed scanned for high-scoring catalyst mentions'],
      ['BUY threshold',            '50 / 100 signal score required to enter the filter funnel'],
      ['SELL threshold',           '55 / 100 — triggers signal-based exit on open positions'],
    ], theme: 'striped', headStyles: TH, bodyStyles: TB, columnStyles: { 0: { fontStyle: 'bold', cellWidth: 55 } }, margin: M })

    y = addSection('Signal Score Components  (BUY, max 100 pts)', nextY())
    autoTable(doc, { startY: y, head: [['Signal', 'Points']], body: [
      ['Price above 50-EMA (long-term uptrend)',    '20'],
      ['Fast EMA > Slow EMA (short-term uptrend)',  '15'],
      ['EMA golden cross this bar',                 '20'],
      ['RSI in 30–75 zone (not overbought)',        '15'],
      ['MACD histogram turning positive',           '15'],
      ['Price in lower half of Bollinger Band',     '5–10'],
      ['Volume 10–25% above 20-day average',        '5–10'],
    ], theme: 'striped', headStyles: TH, bodyStyles: TB, columnStyles: { 0: { fontStyle: 'bold', cellWidth: 110 } }, margin: M })

    // ── Page 3: Filter Funnel & Sizing ──────────────────────────────
    doc.addPage()
    addHeader('Platform Overview — Filters & Position Sizing')
    y = 32

    y = addSection('Step 5 — The 8-Layer Filter Funnel', y)
    autoTable(doc, { startY: y, head: [['#', 'Filter', 'Rule']], body: [
      ['1', 'Sector rotation',       'Symbol\'s sector must be in top 6 of 11 by 3-month ETF momentum'],
      ['2', 'Sector concentration',  'Max 3 open positions per GICS sector'],
      ['3', 'Weekly confirmation',   'Weekly close > 10-week EMA,  RSI 35–75,  MACD positive'],
      ['4', 'Composite sentiment',   '4-source score (options + analyst + insider + retail) > −3.0'],
      ['5', 'Earnings blackout',     'Skip if earnings within 2 days'],
      ['6', 'Correlation limit',     'Skip if Pearson r > 0.70 with any open position'],
      ['7', '13F smart money',       'Score boosted if top 8 hedge funds are adding (Berkshire, Citadel, Renaissance…)'],
      ['8', 'ML confidence',         'LightGBM ≥ 52% probability  (58% in BEAR regime,  65% in HIGH_VOL)'],
    ], theme: 'striped', headStyles: TH, bodyStyles: TB, columnStyles: { 0: { cellWidth: 8 }, 1: { fontStyle: 'bold', cellWidth: 50 } }, margin: M })

    y = addSection('Step 6 — Position Sizing', nextY())
    autoTable(doc, { startY: y, head: [['Layer', 'How it works']], body: [
      ['Kelly Criterion',       'Half-Kelly using win rate & R-ratio once 20+ closed trades exist. Fixed 5% of equity until then.'],
      ['Risk-based sizing',     'shares = (3% of equity) ÷ ATR stop distance.  Never risks more than 3% per trade.'],
      ['Regime multiplier',     'RANGING 0.6×,  BEAR 0.5×,  HIGH_VOL blocked,  BULL 1.0×'],
      ['Volatility targeting',  'Scales down if 20-day annualised vol exceeds 1% per-position budget'],
      ['Cash check',            'Total cost must be ≤ 95% of available cash'],
      ['Stop / Take profit',    'SL = entry − ATR.  TP = entry + (ATR × 3)  →  3:1 reward-to-risk ratio'],
    ], theme: 'striped', headStyles: TH, bodyStyles: TB, columnStyles: { 0: { fontStyle: 'bold', cellWidth: 52 } }, margin: M })

    y = addSection('Step 7 — VWAP Order Execution', nextY())
    autoTable(doc, { startY: y, head: [['Aspect', 'Detail']], body: [
      ['Order type',        'Day limit at VWAP + 0.05% buffer  (never market orders)'],
      ['Urgent buffer',     '0.10% when ML confidence ≥ 70%'],
      ['IS Zero guard',     'Skip entries within first 15 min of open  (9:30–9:45 ET)'],
      ['Stale cancel',      'Auto-cancel if not filled within 3 scans (~30 min)'],
      ['Adaptive buffer',   'Auto-widens to 0.10% if fill rate drops below 60%'],
    ], theme: 'striped', headStyles: TH, bodyStyles: TB, columnStyles: { 0: { fontStyle: 'bold', cellWidth: 50 } }, margin: M })

    // ── Page 4: ML, Sectors & Strategies ────────────────────────────
    doc.addPage()
    addHeader('Platform Overview — ML, Sectors & Strategies')
    y = 32

    y = addSection('Machine Learning Layer', y)
    autoTable(doc, { startY: y, head: [['Aspect', 'Detail']], body: [
      ['Model',           'LightGBM binary classifier  (10–20× faster than RandomForest, better out-of-sample AUC)'],
      ['15 Features',     'RSI, MACD hist, BB position, EMA distances (9/21/50-period), volume ratio, ATR, 5d & 20d momentum, market regime, composite sentiment, signal score, VIX, sector momentum'],
      ['Target',          '1 if forward 5-day return > 0.5%,  else 0'],
      ['Validation',      'Walk-forward CV: 9-month train / 3-month validation windows'],
      ['Retrain',         'Monthly, or after 20+ new closed-trade outcomes accumulated'],
      ['Thresholds',      'BULL/RANGING 52%  |  BEAR 58%  |  HIGH_VOL 65%'],
      ['Fail-safe',       'Bypassed entirely (fail-open) if walk-forward AUC < 0.54'],
    ], theme: 'striped', headStyles: TH, bodyStyles: TB, columnStyles: { 0: { fontStyle: 'bold', cellWidth: 42 } }, margin: M })

    y = addSection('Sector Rotation  (SPDR ETF Rankings)', nextY())
    autoTable(doc, { startY: y, head: [['ETF', 'Sector', 'ETF', 'Sector']], body: [
      ['XLK', 'Technology',             'XLI', 'Industrials'],
      ['XLV', 'Healthcare',             'XLRE','Real Estate'],
      ['XLF', 'Financials',             'XLB', 'Materials'],
      ['XLE', 'Energy',                 'XLU', 'Utilities'],
      ['XLC', 'Communication Services', 'XLP', 'Consumer Staples'],
      ['XLY', 'Consumer Discretionary', '',    ''],
    ], theme: 'striped', headStyles: TH, bodyStyles: TB, columnStyles: { 0: { fontStyle: 'bold', cellWidth: 18 }, 2: { fontStyle: 'bold', cellWidth: 18 } }, margin: M })
    doc.setFontSize(8)
    doc.setFont('helvetica', 'normal')
    doc.setTextColor(100, 100, 100)
    doc.text('Ranked every hour by 3-month price return. Only stocks in top 6 sectors receive buy orders. Max 3 open positions per sector.', 14, nextY() - 4)

    y = addSection('Additional Strategies', nextY())
    autoTable(doc, { startY: y, head: [['Strategy', 'How it works']], body: [
      ['Pairs Trading\n(Stat-Arb)', '13 pre-defined cointegrated pairs (JPM/BAC, XOM/CVX, etc.). Go long cheap / short expensive when z-score > ±2.0. Exit at ±0.5. Stop at ±3.5. Max 2 pairs open, 3% equity per leg.'],
      ['IPO Trading',               'IPOs quarantined 5 days. Queued for human approval in dashboard (60-min window). Auto-execute on timeout. 50% normal size, 4% stop.'],
      ['Short Selling',             'Currently disabled. When enabled: only shorts stocks in bottom-3 sectors with composite sentiment ≤ −2.0. Max 3 concurrent shorts.'],
      ['13F Smart Money',           'Monitors 8 top hedge funds (Berkshire, Citadel, Renaissance, Two Sigma…). Boosts signal score by 2 pts per unit of institutional accumulation detected.'],
    ], theme: 'striped', headStyles: TH, bodyStyles: TB, columnStyles: { 0: { fontStyle: 'bold', cellWidth: 32 } }, margin: M })

    // ── Page 5: Dashboard & Parameters ──────────────────────────────
    doc.addPage()
    addHeader('Platform Overview — Dashboard & Key Parameters')
    y = 32

    y = addSection('Dashboard Tabs', y)
    autoTable(doc, { startY: y, head: [['Tab', 'Contents']], body: [
      ['Overview', 'Portfolio stats, equity curve, open positions, recent trades, market regime, VIX, sector rankings, IPO approvals, pairs positions, bot logs'],
      ['History',  'All closed trades filtered by date (Today / This Week / All Time) and exit reason. Today P&L, win/loss count, trade duration shown.'],
      ['Reports',  'PDF downloads: Performance Summary, Trade History, Strategy Review, Quant Benchmarking, Platform Overview'],
      ['Settings', 'Configure BASE_CAPITAL and withdrawal alert threshold. Persisted to disk across restarts.'],
    ], theme: 'striped', headStyles: TH, bodyStyles: TB, columnStyles: { 0: { fontStyle: 'bold', cellWidth: 28 } }, margin: M })

    y = addSection('Key Configuration Parameters', nextY())
    autoTable(doc, { startY: y, head: [['Parameter', 'Value', 'Purpose']], body: [
      ['BUY_THRESHOLD',           '50',        'Minimum signal score to enter filter funnel'],
      ['SELL_THRESHOLD',          '55',        'Minimum score for signal-based position exit'],
      ['MAX_POSITION_PCT',        '5%',        'Maximum single position size as % of equity'],
      ['STOP_LOSS_PCT',           '3%',        'Hard stop loss & risk-per-trade sizing input'],
      ['BREAK_EVEN_TRIGGER_PCT',  '5%',        'Activate break-even protection once up 5%'],
      ['TRAILING_STOP_PCT',       '4% – 7%',   'Tiered trailing stop by gain level'],
      ['SECTOR_TOP_N',            '6',         'Buy only in top 6 of 11 sectors by momentum'],
      ['MAX_POSITIONS_PER_SECTOR','3',         'Sector concentration cap'],
      ['ML_MIN_PROBABILITY',      '52%–65%',   'Dynamic threshold by market regime'],
      ['VIX_THRESHOLD',           '30',        'Block all new longs above this VIX reading'],
      ['KELLY_MIN_TRADES',        '20',        'Closed trades before Kelly Criterion activates'],
      ['MAX_DAILY_LOSS_PCT',      '5%',        'Daily circuit-breaker — halts new entries'],
      ['PAIRS_ENTRY_ZSCORE',      '2.0',       'Z-score divergence threshold to enter a pair'],
      ['PAIRS_EXIT_ZSCORE',       '0.5',       'Z-score reversion level to exit a pair'],
      ['IPO_MIN_DAYS',            '5',         'Quarantine period before trading new IPOs'],
      ['EARNINGS_BLACKOUT_DAYS',  '2',         'Days before/after earnings to avoid entry'],
      ['MAX_POSITION_CORRELATION','0.70',      'Skip if too correlated with an open position'],
      ['BASE_CAPITAL',            '$100,000',  'Authorised trading pool. Profits above held idle.'],
    ], theme: 'striped', headStyles: TH, bodyStyles: TB, columnStyles: { 0: { fontStyle: 'bold', cellWidth: 65 }, 1: { cellWidth: 22 } }, margin: M })

    // Page numbers
    const pages = doc.internal.getNumberOfPages()
    for (let i = 1; i <= pages; i++) {
      doc.setPage(i)
      doc.setFontSize(8)
      doc.setTextColor(160, 160, 160)
      doc.text(`STOX Platform Overview  ·  ${now}`, 14, doc.internal.pageSize.getHeight() - 8)
      doc.text(`Page ${i} of ${pages}`, W - 14, doc.internal.pageSize.getHeight() - 8, { align: 'right' })
    }

    doc.save(`stox-platform-overview-${new Date().toISOString().slice(0, 10)}.pdf`)
  })

  return (
    <div className="reports-tab">
      <div className="reports-header">
        <h2>Reports</h2>
        <div className="reports-period">
          <span className="reports-period-label">Period:</span>
          {REPORT_PERIODS.map(p => (
            <button
              key={p.days}
              className={`btn ${period === p.days ? 'btn-primary' : 'btn-ghost'} reports-period-btn`}
              onClick={() => setPeriod(p.days)}
            >
              {p.label}
            </button>
          ))}
        </div>
      </div>

      <div className="reports-grid">
        <ReportCard
          title="Performance Summary"
          description="Account overview, realised P&L, win rate, profit factor, Sharpe, Sortino, Calmar, max drawdown, VaR, and top-performing symbols."
          onDownload={downloadPerformance}
          loading={loading.perf}
        />
        <ReportCard
          title="Trade History"
          description="Complete log of all closed trades with entry/exit prices, P&L, percentage return, exit reason, and dates. Landscape A4 format."
          onDownload={downloadTradeHistory}
          loading={loading.trades}
        />
        <ReportCard
          title="Strategy Review"
          description={`Recent vs all-time stats, per-symbol breakdown, and parameter recommendations based on the last ${period} days of live trading.`}
          onDownload={downloadStrategyReview}
          loading={loading.review}
        />
        <ReportCard
          title="Quant Benchmarking Report"
          description="Full system analysis: STOX vs. billion-dollar quant funds. Covers every strategy layer, institutional gaps, performance benchmarks, and ranked improvement priorities."
          onDownload={downloadQuantReport}
          loading={loading.quant}
        />
        <ReportCard
          title="Platform Overview"
          description="Complete 5-page reference: how STOX works end-to-end. Infrastructure, capital management, scan loop, 8-layer filter funnel, ML model, sector rotation, position sizing, VWAP execution, and all key parameters."
          onDownload={downloadPlatformOverview}
          loading={loading.overview}
        />
      </div>

      <div className="reports-note">
        PDFs are generated client-side and downloaded immediately. No data leaves your browser.
      </div>
    </div>
  )
}

export default function Dashboard({ data, onRefresh, onLogout, refreshError }) {
  const { account, positions, trades, summary, equityCurve, botStatus } = data
  const [toggleLoading, setToggleLoading] = useState(false)
  const [botMsg, setBotMsg] = useState(null)
  const [activeTab, setActiveTab] = useState('overview')

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

      <div className="tab-bar">
        <button
          className={`tab-btn ${activeTab === 'overview' ? 'tab-btn-active' : ''}`}
          onClick={() => setActiveTab('overview')}
        >
          Overview
        </button>
        <button
          className={`tab-btn ${activeTab === 'history' ? 'tab-btn-active' : ''}`}
          onClick={() => setActiveTab('history')}
        >
          History
        </button>
        <button
          className={`tab-btn ${activeTab === 'reports' ? 'tab-btn-active' : ''}`}
          onClick={() => setActiveTab('reports')}
        >
          Reports
        </button>
        <button
          className={`tab-btn ${activeTab === 'settings' ? 'tab-btn-active' : ''}`}
          onClick={() => setActiveTab('settings')}
        >
          Settings
        </button>
      </div>

      <main className="main">
        {activeTab === 'overview' && (
          <>
            <StatsRow account={account} summary={summary} posCount={Object.keys(positions).length} />
            <MarketPanel />
            <IPOApprovalPanel />
            <PairsPanel />
            <FeaturesPanel />
            <AnalyticsPanel />
            <EquityChart snapshots={equityCurve} account={account} />
            <div className="tables-grid">
              <PositionsTable positions={positions} />
              <TradesTable trades={trades.slice(0, 20)} />
            </div>
            <LogViewer />
          </>
        )}
        {activeTab === 'history' && (
          <HistoryTab trades={trades} />
        )}
        {activeTab === 'reports' && (
          <ReportsTab data={data} />
        )}
        {activeTab === 'settings' && (
          <SettingsTab account={account} />
        )}
      </main>

      <footer className="footer">
        Auto-refreshes every 30 s &nbsp;·&nbsp; {new Date().toLocaleTimeString()}
      </footer>
    </div>
  )
}
