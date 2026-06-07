import { useCallback, useEffect, useRef, useState } from 'react'
import { ttsUrl } from './api.js'

// A small text-to-speech player hook. Plays one clip at a time and exposes which
// key is currently loading/playing so callers can show an indicator. play()
// resolves to false if synthesis fails or the browser blocks autoplay.
export default function useTts() {
  const audioRef = useRef(null)
  const [playingKey, setPlayingKey] = useState(null)
  const [loadingKey, setLoadingKey] = useState(null)

  useEffect(
    () => () => {
      if (audioRef.current) audioRef.current.pause()
    },
    [],
  )

  const stop = useCallback(() => {
    if (audioRef.current) {
      audioRef.current.pause()
      audioRef.current = null
    }
    setPlayingKey(null)
  }, [])

  const play = useCallback(async (text, key) => {
    const id = key ?? text

    if (audioRef.current) {
      audioRef.current.pause()
      audioRef.current = null
    }
    setPlayingKey(null)
    setLoadingKey(id)

    let url
    try {
      url = await ttsUrl(text)
    } catch {
      setLoadingKey((c) => (c === id ? null : c))
      return false
    }
    setLoadingKey((c) => (c === id ? null : c))

    const audio = new Audio(url)
    audioRef.current = audio
    audio.addEventListener('ended', () => setPlayingKey((c) => (c === id ? null : c)))
    audio.addEventListener('error', () => setPlayingKey((c) => (c === id ? null : c)))

    try {
      await audio.play()
      setPlayingKey(id)
      return true
    } catch {
      // Autoplay blocked or playback error — caller's button is the fallback.
      setPlayingKey((c) => (c === id ? null : c))
      return false
    }
  }, [])

  return { play, stop, playingKey, loadingKey }
}
