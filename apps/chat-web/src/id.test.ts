import { afterEach, describe, expect, it, vi } from 'vitest'

import { createId } from './id'

describe('createId', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('uses randomUUID when the browser provides it', () => {
    vi.stubGlobal('crypto', {
      randomUUID: () => '123e4567-e89b-42d3-a456-426614174000',
    })

    expect(createId()).toBe('123e4567-e89b-42d3-a456-426614174000')
  })

  it('creates a UUID when randomUUID is unavailable on plain HTTP', () => {
    vi.stubGlobal('crypto', {
      getRandomValues: (bytes: Uint8Array) => {
        bytes.set(Array.from({ length: 16 }, (_, index) => index))
        return bytes
      },
    })

    const id = createId()

    expect(id).toBe('00010203-0405-4607-8809-0a0b0c0d0e0f')
    expect(id).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
    )
  })
})
