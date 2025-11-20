import { useEffect, useState } from 'react'

const QUERY = '(max-width: 767px)'

export default function useIsMobile() {
  const [isMobile, setIsMobile] = useState(
    typeof window !== 'undefined' ? window.matchMedia(QUERY).matches : false,
  )

  useEffect(() => {
    if (typeof window === 'undefined') return
    const media = window.matchMedia(QUERY)
    const handleChange = (event: MediaQueryListEvent) => setIsMobile(event.matches)

    // Sync immediately in case the initial render happened before hydration.
    setIsMobile(media.matches)

    if (media.addEventListener) {
      media.addEventListener('change', handleChange)
      return () => media.removeEventListener('change', handleChange)
    }

    // Fallback for older browsers.
    media.addListener(handleChange)
    return () => media.removeListener(handleChange)
  }, [])

  return isMobile
}
