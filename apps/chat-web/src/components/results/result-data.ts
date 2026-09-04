export function asRecord(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {}
}


export function text(value: unknown, fallback = '—'): string {
  if (typeof value === 'string' || typeof value === 'number') return String(value)
  return fallback
}


export function dateTime(value: unknown): string {
  if (typeof value !== 'string') return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN')
}


export function regionName(value: unknown): string {
  const region = asRecord(value)
  return text(region.canonical_name, text(region.raw_text))
}


export function weightLabel(value: unknown): string {
  const weight = asRecord(value)
  const amount = text(weight.value, '')
  const unit = text(weight.unit, '')
  return `${amount} ${unit}`.trim() || '—'
}


export function recordList(value: unknown): Record<string, unknown>[] {
  if (!Array.isArray(value)) return []
  return value.filter(
    (item): item is Record<string, unknown> =>
      item !== null && typeof item === 'object' && !Array.isArray(item),
  )
}


export function safeHttpUrl(value: unknown): string | null {
  if (typeof value !== 'string') return null
  try {
    const url = new URL(value)
    return ['http:', 'https:'].includes(url.protocol) ? url.toString() : null
  } catch {
    return null
  }
}
