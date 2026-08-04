<script setup lang="ts">
import DOMPurify from 'dompurify'
import { marked } from 'marked'
import { computed } from 'vue'

import type { Citation } from '../api'

const props = defineProps<{
  role: 'user' | 'assistant'
  content: string
  state: 'preparing' | 'streaming' | 'done' | 'refused' | 'error' | 'aborted'
  citations: Citation[]
  requestId?: string
  error?: string
  createdAt: number
}>()

marked.use({ gfm: true, breaks: true })

const renderedContent = computed(() =>
  DOMPurify.sanitize(marked.parse(props.content) as string),
)

const time = computed(() =>
  new Intl.DateTimeFormat('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(props.createdAt),
)

const publishedDate = (value: string): string =>
  value ? value.slice(0, 10) : ''
</script>

<template>
  <article class="message" :class="`message--${role}`">
    <div class="message__avatar" aria-hidden="true">
      {{ role === 'assistant' ? '知' : '我' }}
    </div>

    <div class="message__body">
      <div class="message__meta">
        <span>{{ role === 'assistant' ? '政策知识助手' : '我' }}</span>
        <time>{{ time }}</time>
      </div>

      <div class="message__bubble">
        <div
          v-if="content"
          class="message__content markdown-body"
          v-html="renderedContent"
        />

        <div v-if="state === 'preparing'" class="message__status">
          <span class="status-dots" aria-hidden="true"><i /><i /><i /></span>
          正在检索并分析相关资料
        </div>
        <div
          v-else-if="state === 'streaming' && !content"
          class="message__status"
        >
          正在生成回答
        </div>
        <div v-else-if="state === 'aborted'" class="message__status">
          已停止生成
        </div>
        <div v-else-if="state === 'error'" class="message__error" role="alert">
          {{ error || '问答失败，请稍后重试。' }}
        </div>

        <section
          v-if="role === 'assistant' && citations.length"
          class="citations"
          aria-label="参考资料"
        >
          <h3>参考资料</h3>
          <ol>
            <li
              v-for="citation in citations"
              :id="`citation-${citation.index}`"
              :key="citation.chunk_id"
            >
              <div class="citation__heading">
                <span class="citation__index">[{{ citation.index }}]</span>
                <a
                  :href="citation.source_url"
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  {{ citation.title || '未命名政策资料' }}
                </a>
              </div>
              <p v-if="citation.excerpt" class="citation__excerpt">
                {{ citation.excerpt }}
              </p>
              <p class="citation__meta">
                <span v-if="citation.document_no">{{ citation.document_no }}</span>
                <span v-if="citation.source_org">{{ citation.source_org }}</span>
                <span v-if="citation.published_at">
                  {{ publishedDate(citation.published_at) }}
                </span>
                <span v-if="citation.section_path">{{ citation.section_path }}</span>
              </p>
            </li>
          </ol>
        </section>

        <p v-if="requestId && role === 'assistant'" class="message__request-id">
          Request ID: {{ requestId }}
        </p>
      </div>
    </div>
  </article>
</template>
