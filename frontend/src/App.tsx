import { useEffect, useMemo, useState } from 'react'
import {
  HashRouter,
  Link,
  Route,
  Routes,
  useNavigate,
  useParams,
} from 'react-router-dom'

type NewsItem = {
  headline: string
  date: string
  text: string
  path: string
}

const truncate = (text: string, limit = 220) =>
  text.length > limit ? `${text.slice(0, limit).trimEnd()}…` : text

const formatDate = (iso: string) =>
  new Date(iso).toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })

const buildUrl = (filename: string) => `${import.meta.env.BASE_URL}news/${filename}`

function App() {
  const [news, setNews] = useState<NewsItem[]>([])
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const load = async () => {
      try {
        const indexRes = await fetch(buildUrl('index.json'))
        if (!indexRes.ok) throw new Error(`Index load failed (${indexRes.status})`)
        const filenames = (await indexRes.json()) as string[]

        const items = await Promise.all(
          filenames.map(async (filename) => {
            const res = await fetch(buildUrl(filename))
            if (!res.ok) throw new Error(`Fetch failed for ${filename} (${res.status})`)
            const data = (await res.json()) as Omit<NewsItem, 'path'>
            return { ...data, path: filename }
          }),
        )

        const sorted = items.sort((a, b) => b.path.localeCompare(a.path))
        setNews(sorted)
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load news.')
      }
    }

    load()
  }, [])

  return (
    <HashRouter>
      <div className="min-h-screen bg-[#f5f6f8] text-slate-900">
        <div className="mx-auto flex max-w-5xl flex-col gap-6 px-5 py-12 md:px-10">
          <Routes>
            <Route path="/" element={<Feed news={news} error={error} />} />
            <Route
              path="/article/:name"
              element={<Article news={news} onMissingError={setError} />}
            />
            <Route path="*" element={<p className="text-sm text-slate-600">Not found.</p>} />
          </Routes>
        </div>
      </div>
    </HashRouter>
  )
}

function Feed({ news, error }: { news: NewsItem[]; error: string | null }) {
  const isMobile = typeof window !== 'undefined' && window.matchMedia('(max-width: 767px)').matches
  const previewLimit = isMobile ? 140 : 220

  return (
    <>
      <header className="space-y-2 border-b border-slate-200 pb-6">
        <p className="text-xs font-semibold uppercase tracking-[0.25em] text-rose-600">
          Hoglin News
        </p>
        <h1 className="text-3xl font-bold leading-tight text-slate-900 md:text-5xl">
          Latest drops
        </h1>
      </header>

      {error ? (
        <p className="rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-800">
          {error}
        </p>
      ) : news.length === 0 ? (
        <p className="text-sm text-slate-600">Loading drops…</p>
      ) : (
        <div className="flex flex-col gap-4">
          {news.map((item) => (
            <Link key={item.path} to={`/article/${encodeURIComponent(item.path)}`}>
              <article className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm transition hover:border-rose-200 hover:shadow-md md:p-5">
                <h2 className="text-xl font-bold leading-tight text-slate-900 md:text-3xl">
                  {item.headline}
                </h2>
                <p className="mt-2 text-xs leading-relaxed text-slate-700 md:mt-3 md:text-sm">
                  {truncate(item.text, previewLimit)}
                </p>
                <p className="mt-2 text-[10px] font-semibold italic uppercase tracking-wide text-slate-500 md:text-[11px]">
                  {formatDate(item.date)}
                </p>
              </article>
            </Link>
          ))}
        </div>
      )}
    </>
  )
}

function Article({
  news,
  onMissingError,
}: {
  news: NewsItem[]
  onMissingError: (msg: string | null) => void
}) {
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
    <article className="space-y-4 rounded-2xl border border-slate-200 bg-white p-6 shadow-md md:p-8">
      <p className="text-[11px] font-semibold uppercase tracking-[0.2em] text-rose-600">
        Hoglin · Emoji Newswire
      </p>
      <h1 className="text-3xl font-bold leading-tight text-slate-900 md:text-4xl">
        {article.headline}
      </h1>
      <p className="text-xs font-semibold italic uppercase tracking-wide text-slate-500">
        {formatDate(article.date)}
      </p>
      <div className="h-px bg-slate-200" />
      <p className="whitespace-pre-line text-base leading-relaxed text-slate-800">
        {article.text}
      </p>
      <button
        className="inline-flex w-fit items-center gap-2 rounded-full bg-slate-900 px-4 py-2 text-xs font-semibold uppercase tracking-wide text-white shadow transition hover:-translate-y-[1px]"
        onClick={() => navigate('/')}
      >
        ← Back to feed
      </button>
    </article>
  )
}

export default App
