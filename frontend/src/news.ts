export type NewsItem = {
  headline: string
  date: string
  text: string
  path: string
}

export const formatDate = (iso: string) =>
  new Date(iso).toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })

export const buildUrl = (filename: string) => `${import.meta.env.BASE_URL}news/${filename}`
