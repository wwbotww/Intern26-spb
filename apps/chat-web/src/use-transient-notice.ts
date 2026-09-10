import { onScopeDispose, readonly, ref } from 'vue'

export const NOTICE_DURATION_MS = 6000
type PauseReason = 'pointer' | 'focus'

/** One replaceable notice; never use this for blocking errors or required actions. */
export function useTransientNotice(durationMs = NOTICE_DURATION_MS) {
  const message = ref('')
  const paused = new Set<PauseReason>()
  let timer: ReturnType<typeof setTimeout> | null = null
  let remainingMs = durationMs
  let deadline = 0
  let disposed = false

  function cancelTimer(): void {
    if (timer !== null) clearTimeout(timer)
    timer = null
  }

  function dismiss(): void {
    cancelTimer()
    message.value = ''
    paused.clear()
  }

  function schedule(): void {
    if (disposed || !message.value || paused.size) return
    deadline = Date.now() + remainingMs
    timer = setTimeout(dismiss, remainingMs)
  }

  function show(text: string): void {
    if (disposed) return
    cancelTimer()
    message.value = text
    remainingMs = durationMs
    // Calling show with the same text still starts a fresh lifetime.
    if (!text) paused.clear()
    schedule()
  }

  function pause(reason: PauseReason): void {
    if (!message.value || disposed) return
    if (timer !== null) remainingMs = Math.max(0, deadline - Date.now())
    cancelTimer()
    paused.add(reason)
  }

  function resume(reason: PauseReason): void {
    if (!paused.delete(reason) || paused.size) return
    schedule()
  }

  onScopeDispose(() => {
    disposed = true
    dismiss()
  })

  return { message: readonly(message), show, dismiss, pause, resume }
}
