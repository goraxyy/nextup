import { useState } from 'react'

// Sequential ramp from Columbia's blues (lightness falls steadily from empty to full).
const RAMP = ['#F0F0F0', '#B9D9EB', '#6CACE4', '#0077C8', '#1D4F91', '#003087']
const DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']

export function hourLabel(h) {
  const suffix = h < 12 ? 'am' : 'pm'
  return `${h % 12 === 0 ? 12 : h % 12}${suffix}`
}

function rampColor(v) {
  if (v <= 0.02) return RAMP[0]
  const i = Math.min(RAMP.length - 2, Math.floor(v * (RAMP.length - 1)))
  return RAMP[i + 1]
}

function pct(v) {
  return `${Math.round(v * 100)}%`
}

/** Today's expected busyness by hour: one series, one hue; the current hour is navy and labelled. */
export function TodayChart({ today, currentHour }) {
  const [hover, setHover] = useState(null)
  const W = 640, H = 170, top = 22, bottom = 26, left = 30, right = 6
  const band = (W - left - right) / 24
  const barW = Math.min(18, band - 2)
  const y = (v) => top + (H - top - bottom) * (1 - Math.min(1, v))
  const shown = hover ?? currentHour
  const value = today[shown]?.occupancy ?? 0

  return (
    <figure className="chart" aria-label="Expected busyness today, by hour">
      <div className="chart-readout">
        <strong>{pct(value)}</strong> of showers busy
        <span className="muted"> · {hover === null ? 'now' : hourLabel(hover)} on a typical {DAYS[new Date().getDay()]}</span>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-hidden="true" onPointerLeave={() => setHover(null)}>
        {[0.5, 1].map((g) => (
          <g key={g}>
            <line x1={left} x2={W - right} y1={y(g)} y2={y(g)} className="grid" />
            <text x={left - 6} y={y(g) + 4} className="axis" textAnchor="end">{pct(g)}</text>
          </g>
        ))}
        <line x1={left} x2={W - right} y1={y(0)} y2={y(0)} className="baseline" />
        {today.map(({ hour, occupancy }) => {
          const x = left + hour * band + (band - barW) / 2
          const h = Math.max(1, y(0) - y(occupancy))
          const isNow = hour === currentHour
          return (
            <g key={hour}>
              <path d={`M${x},${y(0)} v${-h + 4} q0,-4 4,-4 h${barW - 8} q4,0 4,4 v${h - 4} z`}
                    className={`bar ${isNow ? 'now' : ''} ${hover === hour ? 'hover' : ''}`} />
              {isNow && <text x={x + barW / 2} y={y(occupancy) - 6} className="now-label" textAnchor="middle">Now</text>}
              <rect x={left + hour * band} y={top} width={band} height={H - top - bottom} fill="transparent"
                    onPointerEnter={() => setHover(hour)} />
              {hour % 3 === 0 && <text x={x + barW / 2} y={H - 8} className="axis" textAnchor="middle">{hourLabel(hour)}</text>}
            </g>
          )
        })}
      </svg>
      <div className="sr-only"><table>
        <caption>Expected busyness today</caption>
        <tbody>{today.map(({ hour, occupancy }) => <tr key={hour}><th>{hourLabel(hour)}</th><td>{pct(occupancy)}</td></tr>)}</tbody>
      </table></div>
    </figure>
  )
}

/** A typical week: weekday × hour, darker = busier. */
export function WeekHeatmap({ heatmap }) {
  const [hover, setHover] = useState(null)
  const today = new Date().getDay()
  return (
    <figure className="heatmap" aria-label="A typical week, by weekday and hour">
      <div className="chart-readout">
        {hover
          ? <><strong>{pct(heatmap[hover.d][hover.h])}</strong> busy <span className="muted">· {DAYS[hover.d]} {hourLabel(hover.h)}</span></>
          : <span className="muted">Hover a square to see how busy that hour usually is.</span>}
      </div>
      <div className="heat-grid" onPointerLeave={() => setHover(null)}>
        <span />
        {Array.from({ length: 24 }, (_, h) => <span key={h} className="heat-hour">{h % 6 === 0 ? hourLabel(h) : ''}</span>)}
        {heatmap.map((row, d) => (
          <div key={d} className="heat-row" style={{ display: 'contents' }}>
            <span className={`heat-day ${d === today ? 'today' : ''}`}>{DAYS[d]}</span>
            {row.map((v, h) => (
              <span key={h} className={`heat-cell ${hover?.d === d && hover?.h === h ? 'hover' : ''}`}
                    style={{ background: rampColor(v) }} tabIndex={-1}
                    onPointerEnter={() => setHover({ d, h })}
                    aria-label={`${DAYS[d]} ${hourLabel(h)}: ${pct(v)} busy`} />
            ))}
          </div>
        ))}
      </div>
      <div className="heat-key" aria-hidden="true">
        <span>Quiet</span>
        {RAMP.map((c) => <i key={c} style={{ background: c }} />)}
        <span>Busy</span>
      </div>
      <div className="sr-only"><table>
        <caption>Share of showers busy in a typical week</caption>
        <thead><tr><th>Day</th>{Array.from({ length: 24 }, (_, h) => <th key={h}>{hourLabel(h)}</th>)}</tr></thead>
        <tbody>{heatmap.map((row, d) => <tr key={d}><th>{DAYS[d]}</th>{row.map((v, h) => <td key={h}>{pct(v)}</td>)}</tr>)}</tbody>
      </table></div>
    </figure>
  )
}

/** Estimated water per day, last 7 days: one series; today is navy and labelled. */
export function DailyWater({ daily }) {
  const [hover, setHover] = useState(null)
  if (!daily?.length) return null
  const W = 640, H = 150, top = 22, bottom = 26, left = 44, right = 6
  const max = Math.max(...daily.map((d) => d.gallons), 1)
  const band = (W - left - right) / daily.length
  const barW = Math.min(24, band - 8)
  const y = (v) => top + (H - top - bottom) * (1 - v / max)
  const last = daily.length - 1
  const shown = daily[hover ?? last]
  const dayName = (iso) => new Date(`${iso}T12:00:00`).toLocaleDateString([], { weekday: 'short' })
  const fmt = (n) => n.toLocaleString()

  return (
    <figure className="chart" aria-label="Estimated water use per day, last 7 days">
      <div className="chart-readout">
        <strong>{fmt(shown.gallons)} gal</strong>
        <span className="muted"> · {hover === null || hover === last ? 'today so far' : dayName(shown.day)} · {shown.showers} showers</span>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-hidden="true" onPointerLeave={() => setHover(null)}>
        <line x1={left} x2={W - right} y1={y(max)} y2={y(max)} className="grid" />
        <text x={left - 6} y={y(max) + 4} className="axis" textAnchor="end">{fmt(Math.round(max / 100) * 100)}</text>
        <line x1={left} x2={W - right} y1={y(0)} y2={y(0)} className="baseline" />
        {daily.map((d, i) => {
          const x = left + i * band + (band - barW) / 2
          const h = Math.max(1, y(0) - y(d.gallons))
          const isToday = i === last
          return (
            <g key={d.day}>
              <path d={`M${x},${y(0)} v${-h + 4} q0,-4 4,-4 h${barW - 8} q4,0 4,4 v${h - 4} z`}
                    className={`bar water ${isToday ? 'now' : ''} ${hover === i ? 'hover' : ''}`} />
              {isToday && <text x={x + barW / 2} y={y(d.gallons) - 6} className="now-label" textAnchor="middle">Today</text>}
              <rect x={left + i * band} y={top} width={band} height={H - top - bottom} fill="transparent" onPointerEnter={() => setHover(i)} />
              <text x={x + barW / 2} y={H - 8} className="axis" textAnchor="middle">{dayName(d.day)}</text>
            </g>
          )
        })}
      </svg>
      <div className="sr-only"><table>
        <caption>Estimated water per day</caption>
        <tbody>{daily.map((d) => <tr key={d.day}><th>{d.day}</th><td>{d.gallons} gallons</td><td>{d.showers} showers</td></tr>)}</tbody>
      </table></div>
    </figure>
  )
}
