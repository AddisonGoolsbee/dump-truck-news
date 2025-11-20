import { readdir, writeFile } from 'node:fs/promises'
import path from 'node:path'

const newsDir = path.resolve(new URL('../public/news', import.meta.url).pathname)
const indexPath = path.join(newsDir, 'index.json')

const entries = (await readdir(newsDir)).filter(
  (name) => name.endsWith('.json') && name !== 'index.json',
)

entries.sort((a, b) => b.localeCompare(a))

const json = `${JSON.stringify(entries, null, 2)}\n`
await writeFile(indexPath, json, 'utf8')

console.log(`news index written: ${entries.length} items`)
