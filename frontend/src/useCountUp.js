import { useEffect, useState } from 'react'

// Count a number up from 0 to `target` once `run` is true. Presentation only —
// honours prefers-reduced-motion (then it shows the final value immediately).
export default function useCountUp(target, run = true) {
  const [value, setValue] = useState(0)
  useEffect(() => {
    if (!run) return undefined
    const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
    if (reduce || target <= 0) {
      setValue(target)
      return undefined
    }
    let raf
    const duration = 750
    const start = performance.now()
    const tick = (now) => {
      const t = Math.min(1, (now - start) / duration)
      const eased = 1 - (1 - t) ** 3 // ease-out cubic
      setValue(Math.round(target * eased))
      if (t < 1) raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [target, run])
  return value
}
