import { useEffect, useMemo, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'

import { buildUrl, formatDate, type NewsItem } from '../news'

type Props = {
  news: NewsItem[]
  onMissingError: (msg: string | null) => void
}

function ArticlePage({ news, onMissingError }: Props) {
  const { name } = useParams<{ name: string }>()
  const navigate = useNavigate()
  const decoded = useMemo(() => (name ? decodeURIComponent(name) : ''), [name])
  const [article, setArticle] = useState<NewsItem | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    onMissingError(null)
    if (!decoded) return
    const existing = news.find((n) => n.path === decoded)
    if (existing) {
      setArticle(existing)
      return
    }
    setLoading(true)
    fetch(buildUrl(decoded))
      .then(async (res) => {
        if (!res.ok) throw new Error(`Fetch failed (${res.status})`)
        const data = (await res.json()) as Omit<NewsItem, 'path'>
        setArticle({ ...data, path: decoded })
      })
      .catch((err: unknown) => {
        const msg = err instanceof Error ? err.message : 'Failed to load article.'
        setError(msg)
        onMissingError(msg)
      })
      .finally(() => setLoading(false))
  }, [decoded, news, onMissingError])

  if (!decoded) {
    return <p className="text-sm text-slate-600">Not found.</p>
  }

  if (loading) {
    return <p className="text-sm text-slate-600">Loading article…</p>
  }

  if (error || !article) {
    return (
      <div className="space-y-3">
        <p className="rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-800">
          {error ?? 'Article not found.'}
        </p>
        <button
          className="inline-flex w-fit items-center gap-2 rounded-full bg-slate-900 px-4 py-2 text-xs font-semibold uppercase tracking-wide text-white shadow transition hover:-translate-y-[1px]"
          onClick={() => navigate('/')}
        >
          ← Back to feed
        </button>
      </div>
    )
  }

  return (
    <article className="space-y-4 pb-10 md:pb-14">
      <p className="text-[10px] font-semibold uppercase tracking-[0.2em] text-rose-600 md:text-[11px]">
        Schizo News
      </p>
      <h1 className="text-2xl font-bold leading-snug text-slate-900 md:text-4xl">
        {article.headline}
      </h1>
      <p className="text-[11px] font-semibold italic uppercase tracking-wide text-slate-500 md:text-xs">
        {formatDate(article.date)}
      </p>
      <div className="h-px bg-slate-200" />
      <p className="whitespace-pre-line text-sm leading-relaxed text-slate-800 md:text-base">
        {article.text}
      </p>
      <button
        className="inline-flex w-fit items-center gap-2 rounded-full bg-slate-900 px-4 py-2 text-xs font-semibold uppercase tracking-wide text-white shadow transition hover:-translate-y-[1px] md:px-5 md:py-2.5"
        onClick={() => navigate('/')}
      >
        ← Back to feed
      </button>
    </article>
  )
}

export default ArticlePage
