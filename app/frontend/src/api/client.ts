// Minimal JSON fetch wrapper. Surfaces non-2xx as a typed error the UI can show.

export class ApiError extends Error {
  constructor(public status: number, public body: string) {
    super(`HTTP ${status}: ${body}`)
    this.name = 'ApiError'
  }
}

export async function jsonFetch<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
  if (!res.ok) {
    throw new ApiError(res.status, await res.text())
  }
  // 202/204 may have an empty body; tolerate it.
  const text = await res.text()
  return (text ? JSON.parse(text) : undefined) as T
}
