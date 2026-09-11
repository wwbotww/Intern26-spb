import type { PriceCandidateOption, ProductPriceResponseData } from './generated/agent-api'

function assert(value: unknown): asserts value {
  if (!value) throw new Error('商品价格数据未通过契约校验。')
}
function object(value: unknown, keys: string[]): Record<string, unknown> {
  assert(value && typeof value === 'object' && !Array.isArray(value))
  const row = value as Record<string, unknown>
  assert(Object.keys(row).every((key) => keys.includes(key)))
  return row
}
function text(value: unknown): asserts value is string {
  assert(typeof value === 'string' && value.length > 0 && value.length <= 255)
}
function time(value: unknown): number {
  assert(typeof value === 'string' && /(?:Z|[+-]\d{2}:\d{2})$/.test(value))
  const result = Date.parse(value)
  assert(Number.isFinite(result))
  return result
}
function amount(value: unknown): bigint {
  assert(typeof value === 'string' && /^[0-9]{1,18}\.[0-9]{2,6}$/.test(value))
  const [whole, fraction = ''] = value.split('.')
  return BigInt(whole) * 1000000n + BigInt(fraction.padEnd(6, '0'))
}
function one(value: unknown, allowed: string[]): void {
  assert(typeof value === 'string' && allowed.includes(value))
}
function quote(value: unknown, kind: unknown): Record<string, unknown> {
  const row = object(value, ['kind', 'currency', 'current_price', 'original_price', 'original_price_type',
    'availability', 'price_nature', 'quoted_unit', 'unit_price', 'observed_at', 'time_precision'])
  one(row.kind, ['priced', 'availability_only'])
  assert(row.currency === 'CNY')
  one(row.availability, ['ON_SALE', 'OUT_OF_STOCK', 'RESERVATION', 'PRE_SALE', 'COMING_SOON', 'OFF_SHELF', 'UNKNOWN'])
  one(row.price_nature, kind === 'device' ? ['RETAIL_OFFER'] : ['RETAIL_AVERAGE', 'WHOLESALE_AVERAGE'])
  one(row.quoted_unit, kind === 'device' ? ['CNY_PER_PIECE'] : ['CNY_PER_500G', 'CNY_PER_KG'])
  one(row.time_precision, ['DAY', 'INSTANT', 'UNKNOWN'])
  one(row.original_price_type, ['NONE', 'CROSSED_OUT', 'MSRP', 'EXPLICIT_ORIGINAL'])
  time(row.observed_at)
  assert((row.original_price === null) === (row.original_price_type === 'NONE'))
  if (row.original_price !== null) assert(amount(row.original_price) > 0n)
  if (row.kind === 'availability_only') {
    assert(row.current_price === null && row.unit_price === null && row.original_price === null)
    one(row.availability, ['OUT_OF_STOCK', 'OFF_SHELF', 'COMING_SOON'])
  } else {
    const raw = amount(row.current_price)
    assert(raw > 0n)
    if (row.unit_price !== null) {
      const normalized = object(row.unit_price, ['amount', 'unit'])
      assert(normalized.unit === (kind === 'device' ? 'CNY_PER_PIECE' : 'CNY_PER_KG'))
      assert(amount(normalized.amount) === raw * (row.quoted_unit === 'CNY_PER_500G' ? 2n : 1n))
    } else assert(kind === 'device')
  }
  return row
}

export function validatePriceCandidates(value: unknown): PriceCandidateOption[] {
  if (value === undefined) return []
  assert(Array.isArray(value) && value.length <= 20)
  const tokens = new Set<string>()
  for (const raw of value) {
    const row = object(raw, ['candidate_token', 'label', 'expires_at'])
    assert(typeof row.candidate_token === 'string' && /^[A-Za-z0-9_-]{43}$/.test(row.candidate_token))
    assert(!tokens.has(row.candidate_token))
    tokens.add(row.candidate_token)
    text(row.label)
    time(row.expires_at) // Expired snapshots may render, but cannot be selected.
  }
  return value as PriceCandidateOption[]
}

export function validateProductPrice(value: unknown): ProductPriceResponseData {
  const data = object(value, ['type', 'schema_version', 'items', 'truncated'])
  assert(data.type === 'product_price' && data.schema_version === '1' && typeof data.truncated === 'boolean')
  assert(Array.isArray(data.items) && data.items.length >= 1 && data.items.length <= 100)
  for (const raw of data.items) {
    const item = object(raw, ['identity', 'quote', 'source', 'region', 'queried_at', 'freshness', 'last_known_price'])
    const identity = object(item.identity, ['kind', 'brand', 'product_name', 'specification', 'commodity_name', 'source_specification', 'market_name'])
    one(identity.kind, ['device', 'fresh'])
    if (identity.kind === 'device') {
      object(identity, ['kind', 'brand', 'product_name', 'specification'])
      text(identity.brand); text(identity.product_name)
      assert(identity.specification && typeof identity.specification === 'object' && !Array.isArray(identity.specification))
      const spec = identity.specification as Record<string, unknown>
      assert(Object.keys(spec).length <= 23)
      Object.values(spec).forEach(text)
    } else {
      object(identity, ['kind', 'commodity_name', 'source_specification', 'market_name'])
      text(identity.commodity_name); text(identity.source_specification)
      if (identity.market_name != null) text(identity.market_name)
    }
    const current = quote(item.quote, identity.kind)
    assert(time(current.observed_at) <= time(item.queried_at))
    one(item.freshness, ['unknown', 'fresh', 'stale'])
    const source = object(item.source, ['name', 'profile', 'url'])
    text(source.name)
    assert(typeof source.profile === 'string' && /^[A-Z0-9_.-]{1,64}$/.test(source.profile))
    assert(typeof source.url === 'string')
    const url = new URL(source.url)
    assert(['http:', 'https:'].includes(url.protocol) && !url.username && !url.password)
    const region = object(item.region, ['scope', 'code'])
    one(region.scope, ['NATIONAL', 'PROVINCE', 'CITY', 'DISTRICT', 'DELIVERY_ZONE']); text(region.code)
    if (item.last_known_price != null) {
      const history = quote(item.last_known_price, identity.kind)
      assert(current.kind === 'availability_only' && history.kind === 'priced')
      assert(history.quoted_unit === current.quoted_unit && history.price_nature === current.price_nature)
      assert(time(history.observed_at) < time(current.observed_at))
    }
  }
  return value as ProductPriceResponseData
}
