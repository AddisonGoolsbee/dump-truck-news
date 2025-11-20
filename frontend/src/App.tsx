import { useEffect, useState } from 'react'

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
    <div className="min-h-screen bg-[#f5f6f8] text-slate-900">
      <div className="mx-auto flex max-w-5xl flex-col gap-6 px-5 py-12 md:px-10">
        <header className="space-y-2 border-b border-slate-200 pb-6">
          <p className="text-xs font-semibold uppercase tracking-[0.25em] text-rose-600">
            Hoglin News
          </p>
          <h1 className="text-4xl font-bold md:text-5xl text-slate-900">
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
              <article
                key={item.path}
                className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm transition hover:border-rose-200 hover:shadow-md"
              >
                <h2 className="text-2xl font-bold leading-tight text-slate-900 md:text-3xl">
                  {item.headline}
                </h2>
                <p className="mt-3 text-sm text-slate-700">{truncate(item.text)}</p>
                <p className="mt-2 text-[11px] font-semibold italic uppercase tracking-wide text-slate-500">
                  {formatDate(item.date)}
                </p>
              </article>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

export default App
