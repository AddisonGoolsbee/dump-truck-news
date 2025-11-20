const highlights = [
  'React + TypeScript via Vite',
  'Tailwind CSS already wired up',
  'Hot reload out of the box',
]

function App() {
  return (
    <div className="min-h-screen bg-slate-950 text-slate-50">
      <div className="mx-auto flex max-w-5xl flex-col gap-10 px-6 py-16 md:px-10">
        <header className="space-y-4">
          <p className="text-sm font-semibold uppercase tracking-[0.3em] text-cyan-300">
            Hoglin
          </p>
          <h1 className="text-4xl font-bold md:text-5xl">
            Emoji News starter
          </h1>
          <p className="max-w-2xl text-lg text-slate-300">
            A fresh Vite + React + TypeScript stack with Tailwind preconfigured.
            Start the dev server and ship your first headline.
          </p>
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
            <a
              className="inline-flex items-center justify-center rounded-full bg-cyan-400 px-5 py-2.5 text-sm font-semibold text-slate-950 shadow-lg shadow-cyan-400/30 transition hover:translate-y-[-1px] hover:shadow-cyan-400/50"
              href="https://vite.dev/guide/"
              target="_blank"
              rel="noreferrer"
            >
              Vite guide
            </a>
            <a
              className="inline-flex items-center justify-center rounded-full border border-white/20 px-5 py-2.5 text-sm font-semibold text-slate-50 transition hover:border-white/40 hover:bg-white/5"
              href="https://tailwindcss.com/docs/"
              target="_blank"
              rel="noreferrer"
            >
              Tailwind docs
            </a>
          </div>
        </header>

        <section className="grid gap-6 rounded-2xl border border-white/10 bg-white/5 p-6 shadow-xl backdrop-blur md:grid-cols-3">
          {highlights.map((item) => (
            <div
              key={item}
              className="rounded-xl border border-white/10 bg-slate-900/60 p-4 text-sm font-medium text-slate-100 shadow"
            >
              {item}
            </div>
          ))}
        </section>

        <section className="grid gap-4 rounded-2xl border border-white/10 bg-gradient-to-br from-cyan-500/20 via-slate-900 to-slate-950 p-6 shadow-xl">
          <h2 className="text-2xl font-semibold text-white">Try it out</h2>
          <ol className="space-y-3 text-sm text-slate-200">
            <li>1. Run `pnpm dev`</li>
            <li>2. Open the provided localhost URL</li>
            <li>3. Edit `src/App.tsx` and `src/index.css` to start building</li>
          </ol>
          <p className="text-xs uppercase tracking-[0.2em] text-cyan-200">
            Happy shipping!
          </p>
        </section>
      </div>
    </div>
  )
}

export default App
