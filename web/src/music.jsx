import { useEffect, useState } from 'react'
import { clock } from './lib.js'

/** Seconds into the song, on the server's clock (offset = server time − this device's time). */
export function songPosition(np, offset) {
  if (!np) return 0
  if (!np.playing) return np.position_s || 0
  return (Date.now() + offset - new Date(np.started_at).getTime()) / 1000
}

export function currentLine(lyrics, position) {
  if (!lyrics?.length) return -1
  let i = -1
  for (let k = 0; k < lyrics.length; k++) {
    if (lyrics[k][0] <= position) i = k
    else break
  }
  return i
}

/** "Now playing in Room 1": the song from the speaker, with the lyric line you're hearing. */
export function NowPlaying({ music, offset, roomName }) {
  const [, setFrame] = useState(0)
  const np = music?.now_playing
  useEffect(() => {
    if (!np?.playing) return
    const timer = setInterval(() => setFrame((n) => n + 1), 200)
    return () => clearInterval(timer)
  }, [np?.playing, np?.started_at])

  if (!np) {
    return (
      <section className="card music idle">
        <p className="kicker">Now playing in {roomName}</p>
        <p className="muted">Nothing is playing on the speaker right now.</p>
      </section>
    )
  }
  const position = songPosition(np, offset)
  const lyrics = music.lyrics
  const i = currentLine(lyrics, position)
  const duration = np.duration_s || 0
  return (
    <section className="card music" aria-live="off">
      <p className="kicker">{np.playing ? 'Now playing' : 'Paused'} in {roomName}</p>
      <h2 className="song">{np.title}</h2>
      <p className="artist">{np.artist}</p>
      {lyrics ? (
        <div className="lyrics">
          <p className="line prev">{i > 0 ? lyrics[i - 1][1] : ''}</p>
          <p className="line current">{i >= 0 ? (lyrics[i][1] || '♪') : '♪'}</p>
          <p className="line next">{lyrics[i + 1]?.[1] || ''}</p>
        </div>
      ) : (
        <p className="muted no-lyrics">No synced lyrics for this song.</p>
      )}
      {duration > 0 && (
        <div className="progress" aria-label={`${clock(position)} of ${clock(duration)}`}>
          <div className="bar"><i style={{ width: `${Math.min(100, (position / duration) * 100)}%` }} /></div>
          <span>{clock(Math.min(position, duration))}</span><span>{clock(duration)}</span>
        </div>
      )}
    </section>
  )
}
