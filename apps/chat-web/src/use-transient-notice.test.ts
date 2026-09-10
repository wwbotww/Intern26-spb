import { effectScope } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { NOTICE_DURATION_MS, useTransientNotice } from './use-transient-notice'

describe('transient notice lifecycle', () => {
  const scopes: ReturnType<typeof effectScope>[] = []
  function notice() {
    const scope = effectScope()
    scopes.push(scope)
    return scope.run(() => useTransientNotice())!
  }
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => {
    scopes.splice(0).forEach((scope) => scope.stop())
    vi.useRealTimers()
  })

  it('dismisses after six seconds without an action', () => {
    const item = notice()
    item.show('已恢复会话')
    vi.advanceTimersByTime(NOTICE_DURATION_MS - 1)
    expect(item.message.value).toBe('已恢复会话')
    vi.advanceTimersByTime(1)
    expect(item.message.value).toBe('')
    expect(vi.getTimerCount()).toBe(0)
  })

  it.each(['新的提示', '已恢复会话'])('replaces the previous timer, including repeated text: %s', (text) => {
    const item = notice()
    item.show('已恢复会话')
    vi.advanceTimersByTime(5000)
    item.show(text)
    expect(vi.getTimerCount()).toBe(1)
    vi.advanceTimersByTime(1000)
    expect(item.message.value).toBe(text)
    vi.advanceTimersByTime(5000)
    expect(item.message.value).toBe('')
  })

  it('preserves reading time until both hover and keyboard focus leave', () => {
    const item = notice()
    item.show('可暂停阅读的提示')
    vi.advanceTimersByTime(2000)
    item.pause('pointer')
    item.pause('focus')
    vi.advanceTimersByTime(20_000)
    item.resume('pointer')
    vi.advanceTimersByTime(20_000)
    expect(item.message.value).not.toBe('')
    expect(vi.getTimerCount()).toBe(0)
    item.resume('focus')
    item.resume('focus')
    expect(vi.getTimerCount()).toBe(1)
    vi.advanceTimersByTime(3999)
    expect(item.message.value).not.toBe('')
    vi.advanceTimersByTime(1)
    expect(item.message.value).toBe('')
  })

  it('gives a replacement a full lifetime after a paused reader leaves', () => {
    const item = notice()
    item.show('旧提示')
    vi.advanceTimersByTime(5000)
    item.pause('pointer')
    item.show('新提示')
    vi.advanceTimersByTime(10_000)
    expect(item.message.value).toBe('新提示')
    item.resume('pointer')
    vi.advanceTimersByTime(NOTICE_DURATION_MS)
    expect(item.message.value).toBe('')
  })

  it('manual close cancels the timer and clears stale pause state', () => {
    const item = notice()
    item.show('旧提示')
    item.pause('focus')
    item.dismiss()
    expect(item.message.value).toBe('')
    expect(vi.getTimerCount()).toBe(0)
    item.show('新提示')
    vi.advanceTimersByTime(NOTICE_DURATION_MS)
    expect(item.message.value).toBe('')
  })

  it('cancels on scope disposal and ignores late async updates', () => {
    const item = notice()
    item.show('提示')
    scopes.at(-1)!.stop()
    expect(vi.getTimerCount()).toBe(0)
    item.show('组件卸载后的回调')
    item.resume('focus')
    expect(item.message.value).toBe('')
    expect(vi.getTimerCount()).toBe(0)
  })

  it('does not schedule an empty notice', () => {
    const item = notice()
    item.show('旧提示')
    item.show('')
    item.pause('pointer')
    item.resume('pointer')
    expect(item.message.value).toBe('')
    expect(vi.getTimerCount()).toBe(0)
  })
})
