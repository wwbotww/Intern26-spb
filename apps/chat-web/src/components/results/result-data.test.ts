import { describe, expect, it } from 'vitest'

import { recordList, safeHttpUrl } from './result-data'


describe('Agent evidence result helpers', () => {
  it('keeps only object evidence records', () => {
    expect(recordList([{ evidence_id: 'policy-1' }, null, [], 'bad'])).toEqual([
      { evidence_id: 'policy-1' },
    ])
    expect(recordList('not-an-array')).toEqual([])
  })

  it('only exposes HTTP(S) source links', () => {
    expect(safeHttpUrl('https://example.test/policy')).toBe(
      'https://example.test/policy',
    )
    expect(safeHttpUrl('http://example.test/device')).toBe(
      'http://example.test/device',
    )
    expect(safeHttpUrl('javascript:alert(1)')).toBeNull()
    expect(safeHttpUrl('/relative/path')).toBeNull()
  })
})
