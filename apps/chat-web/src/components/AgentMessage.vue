<script setup lang="ts">
import DOMPurify from 'dompurify'
import { marked } from 'marked'
import { computed } from 'vue'

import type { AgentUiMessage } from '../agent-ui-model'
import AgentResultRenderer from './results/AgentResultRenderer.vue'


const props = defineProps<{ message: AgentUiMessage }>()
marked.use({ gfm: true, breaks: true })

const renderedContent = computed(() =>
  DOMPurify.sanitize(marked.parse(props.message.content) as string),
)
const timeLabel = computed(() =>
  new Date(props.message.createdAt).toLocaleTimeString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
  }),
)
const intentLabel = computed(() =>
  props.message.intent
    ? {
        policy: '政策查询',
        device_price: '设备价格',
        tracking: '邮件轨迹',
        delivery_time: '寄递时限',
        postage: '邮费试算',
      }[props.message.intent]
    : '',
)
</script>

<template>
  <article
    class="message"
    :class="message.role === 'user' ? 'message--user' : 'message--assistant'"
  >
    <div class="message__avatar" aria-hidden="true">
      {{ message.role === 'user' ? '您' : '邮' }}
    </div>
    <div class="message__body">
      <p class="message__meta">
        <span>{{ message.role === 'user' ? '访客' : '理赔 Agent' }}</span>
        <span v-if="intentLabel">{{ intentLabel }}</span>
        {{ timeLabel }}
      </p>
      <div class="message__bubble">
        <div
          v-if="message.content"
          class="message__content markdown-body"
          v-html="renderedContent"
        />
        <div
          v-else-if="['preparing', 'streaming'].includes(message.state)"
          class="message__status"
        >
          <span class="status-dots" aria-hidden="true"><i /><i /><i /></span>
          {{ message.statusText || '正在推进 Agent 工作流' }}
        </div>
        <div v-if="message.state === 'error'" class="message__error" role="alert">
          {{ message.error || '本轮执行失败，请稍后重试。' }}
        </div>
        <div v-else-if="message.state === 'aborted'" class="result-notice result-notice--neutral">
          已停止读取响应；可以使用原幂等键安全重试。
        </div>
        <div v-else-if="message.state === 'waiting_user'" class="result-notice result-notice--info">
          工作流已暂停并持久化，补充信息后会从 checkpoint 继续。
        </div>
        <div v-else-if="message.state === 'handoff'" class="result-notice result-notice--warning">
          当前请求需要人工或其他能力继续处理。
        </div>
        <div v-else-if="message.state === 'partial'" class="result-notice result-notice--warning">
          上游只返回了部分结果，请结合提示核对。
        </div>

        <AgentResultRenderer v-if="message.result" :result="message.result" />

        <section v-if="message.warnings.length" class="result-details" aria-label="结果提示">
          <p v-for="warning in message.warnings" :key="warning">{{ warning }}</p>
        </section>

        <p v-if="message.requestId" class="message__request-id">
          Request ID: {{ message.requestId }}
          <span v-if="message.failure?.code"> · {{ message.failure.code }}</span>
        </p>
      </div>
    </div>
  </article>
</template>
