export const S = () => window.SCHRIFT || { strings: {}, lang: 'nl' }
export const tr = (s) => S().strings?.[s] ?? s

export async function api(method, url, body) {
  const opts = { method, headers: { Accept: 'application/json' } }
  if (body instanceof FormData) opts.body = body
  else if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json'
    opts.body = JSON.stringify(body)
  }
  const res = await fetch(url, opts)
  let data = null
  try {
    data = await res.json()
  } catch {
    data = null
  }
  if (res.status === 401 && data?.login) {
    window.location.href = data.login
  }
  return { ok: res.ok, status: res.status, data }
}

