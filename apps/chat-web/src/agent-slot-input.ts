import type { RequiredInput } from './agent-api'

// A confirmation reply is standalone; the server binds it to reviewed conditions.
export function slotReply(inputs: RequiredInput[], values: Record<string, string>): string {
  if (inputs.some((input) => input.name === 'postage_confirmation')) {
    if (inputs.length !== 1 || !(inputs[0].choices ?? []).includes(values.postage_confirmation ?? '')) return ''
    return values.postage_confirmation
  }
  if (inputs.some((input) => !(values[input.name] ?? '').trim()
    || (input.type === 'choice' && !(input.choices ?? []).includes(values[input.name])))) return ''
  return inputs.map((input) => `${input.label}：${values[input.name].trim()}`).join('；')
}
