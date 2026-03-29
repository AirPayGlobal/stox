import { useState, useEffect, useRef } from 'react'
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid,
  Tooltip, ResponsiveContainer,
} from 'recharts'
import { startBot, stopBot, fetchLogs, fetchPendingTrades, approveTrade, declineTrade, fetchPairs, fetchAnalytics, fetchRegime, fetchFeatures, fetchMarket } from './api.js'

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
      fetch('/api/market-status')
        .then(r => r.json())
        .then(d => setIsOpen(d.is_open))
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
        <MarketPanel />
        <IPOApprovalPanel />
        <PairsPanel />
        <FeaturesPanel />
        <AnalyticsPanel />
        <EquityChart snapshots={equityCurve} />
        <div className="tables-grid">
          <PositionsTable positions={positions} />
          <TradesTable trades={trades.slice(0, 20)} />
        </div>
        <LogViewer />
      </main>

      <footer className="footer">
        Auto-refreshes every 30 s &nbsp;·&nbsp; {new Date().toLocaleTimeString()}
      </footer>
    </div>
  )
}
