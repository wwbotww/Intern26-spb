<script setup lang="ts">
import DOMPurify from 'dompurify'
import { marked } from 'marked'
import { computed } from 'vue'

import type {
  DevicePriceEvidence,
  Evidence,
  PolicyEvidence,
  QueryMode,
} from '../api'

const props = defineProps<{
  role: 'user' | 'assistant'
  mode: QueryMode
  content: string
  state:
    | 'preparing'
    | 'streaming'
    | 'done'
    | 'partial'
    | 'needs_info'
    | 'refused'
    | 'error'
    | 'aborted'
  evidence: Evidence[]
  warnings: string[]
  missingFields: string[]
  statusText?: string
  requestId?: string
  reasonCode?: string
  error?: string
  createdAt: number
}>()

marked.use({ gfm: true, breaks: true })

const renderedContent = computed(() =>
  DOMPurify.sanitize(marked.parse(props.content) as string),
)

const policyEvidence = computed(() =>
  props.evidence.filter(
    (item): item is PolicyEvidence => item.type === 'policy',
  ),
)

const priceEvidence = computed(() =>
  props.evidence.filter(
    (item): item is DevicePriceEvidence => item.type === 'device_price',
  ),
)

const assistantName = computed(() =>
  props.mode === 'policy' ? '政策咨询助手' : '设备价格助手',
)

const time = computed(() =>
  new Intl.DateTimeFormat('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(props.createdAt),
)

const missingFieldLabels: Record<string, string> = {
  brand_or_model: '品牌或完整型号',
  matching_specification: '匹配的容量或内存规格',
  single_query_category: '单一查询类别',
}

function publishedDate(value: string): string {
  return value ? value.slice(0, 10) : ''
}

function observedTime(value: string): string {
  if (!value) return '未知'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(date)
}

function currencyPrice(value: string, currency: string): string {
  const amount = Number(value)
  if (!Number.isFinite(amount)) return `${value} ${currency}`.trim()
  try {
    return new Intl.NumberFormat('zh-CN', {
      style: 'currency',
      currency: currency || 'CNY',
      minimumFractionDigits: 2,
    }).format(amount)
  } catch {
    return `${value} ${currency}`.trim()
  }
}

function availabilityLabel(value: string): string {
  const labels: Record<string, string> = {
    ON_SALE: '在售',
    RESERVATION: '预约',
    PRE_SALE: '预售',
    OUT_OF_STOCK: '缺货',
    OFF_SHELF: '已下架',
    UNKNOWN: '状态未知',
  }
  return labels[value] || value || '状态未知'
}

function safeExternalUrl(value: string): string | undefined {
  if (!value) return undefined
  try {
    const url = new URL(value)
    return ['http:', 'https:'].includes(url.protocol) ? url.href : undefined
  } catch {
    return undefined
  }
}

function missingFieldLabel(value: string): string {
  return missingFieldLabels[value] ?? value
}
</script>

<template>
  <article class="message" :class="`message--${role}`">
    <div class="message__avatar" aria-hidden="true">
      {{ role === 'assistant' ? (mode === 'policy' ? '政' : '价') : '我' }}
    </div>

    <div class="message__body">
      <div class="message__meta">
        <span>{{ role === 'assistant' ? assistantName : '我' }}</span>
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
          {{ statusText || '正在查询资料' }}
        </div>
        <div
          v-else-if="state === 'streaming' && !content"
          class="message__status"
        >
          正在整理查询结果
        </div>
        <div v-else-if="state === 'aborted'" class="message__status">
          已停止本次查询
        </div>
        <div v-else-if="state === 'error'" class="message__error" role="alert">
          {{ error || '查询失败，请稍后重试。' }}
        </div>

        <div
          v-if="state === 'partial'"
          class="result-notice result-notice--warning"
        >
          当前结果仅覆盖部分信息，请结合提示和来源继续核对。
        </div>
        <div
          v-else-if="state === 'needs_info'"
          class="result-notice result-notice--info"
        >
          现有信息不足，请补充下列内容后重新发起一次查询。
        </div>
        <div
          v-else-if="state === 'refused'"
          class="result-notice result-notice--neutral"
        >
          当前数据源没有找到足够可靠的依据，因此未给出推测性结果。
        </div>

        <section
          v-if="warnings.length || missingFields.length"
          class="result-details"
          aria-label="结果提示"
        >
          <p v-for="warning in warnings" :key="warning">
            {{ warning }}
          </p>
          <div v-if="missingFields.length" class="missing-fields">
            <span>需要补充：</span>
            <strong v-for="field in missingFields" :key="field">
              {{ missingFieldLabel(field) }}
            </strong>
          </div>
        </section>

        <section
          v-if="role === 'assistant' && policyEvidence.length"
          class="citations"
          aria-label="政策参考资料"
        >
          <h3>政策参考资料</h3>
          <ol>
            <li
              v-for="(citation, index) in policyEvidence"
              :id="`citation-${citation.evidence_id}`"
              :key="citation.evidence_id"
            >
              <div class="citation__heading">
                <span class="citation__index">[{{ index + 1 }}]</span>
                <a
                  v-if="safeExternalUrl(citation.source_url)"
                  :href="safeExternalUrl(citation.source_url)"
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  {{ citation.title || '未命名政策资料' }}
                </a>
                <span v-else>{{ citation.title || '未命名政策资料' }}</span>
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

        <section
          v-if="role === 'assistant' && priceEvidence.length"
          class="price-results"
          aria-label="设备价格候选"
        >
          <div class="price-results__heading">
            <h3>设备价格候选</h3>
            <span>{{ priceEvidence.length }} 条记录</span>
          </div>
          <p v-if="priceEvidence.length > 1" class="price-results__note">
            存在多个可能价格，请根据完整型号、规格和 SKU 进一步核对。
          </p>
          <div class="price-grid">
            <article
              v-for="item in priceEvidence"
              :key="item.evidence_id"
              class="price-card"
            >
              <div class="price-card__top">
                <div>
                  <p class="price-card__brand">{{ item.brand }}</p>
                  <h4>{{ item.title || item.model }}</h4>
                </div>
                <span class="availability-badge">
                  {{ availabilityLabel(item.availability) }}
                </span>
              </div>
              <div class="price-card__price">
                <strong>{{ currencyPrice(item.price, item.currency) }}</strong>
                <del v-if="item.original_price">
                  {{ currencyPrice(item.original_price, item.currency) }}
                </del>
              </div>
              <dl>
                <div v-if="item.specification">
                  <dt>规格</dt>
                  <dd>{{ item.specification }}</dd>
                </div>
                <div>
                  <dt>数据来源</dt>
                  <dd>{{ item.source || '未标明' }}</dd>
                </div>
                <div>
                  <dt>观察时间</dt>
                  <dd>{{ observedTime(item.observed_at) }}</dd>
                </div>
                <div>
                  <dt>匹配度</dt>
                  <dd>{{ item.match_score.toFixed(1) }}</dd>
                </div>
              </dl>
              <p
                v-if="item.official_product_id || item.official_sku_id"
                class="price-card__ids"
              >
                <span v-if="item.official_product_id">
                  Product: {{ item.official_product_id }}
                </span>
                <span v-if="item.official_sku_id">
                  SKU: {{ item.official_sku_id }}
                </span>
              </p>
              <a
                v-if="safeExternalUrl(item.source_url)"
                class="price-card__link"
                :href="safeExternalUrl(item.source_url)"
                target="_blank"
                rel="noopener noreferrer"
              >
                查看数据来源 →
              </a>
            </article>
          </div>
        </section>

        <p
          v-if="
            role === 'assistant' &&
            ['done', 'partial', 'needs_info', 'refused'].includes(state)
          "
          class="answer-disclaimer"
        >
          以上为参考信息，不构成最终定损、赔付或审批结论。
        </p>

        <p v-if="requestId && role === 'assistant'" class="message__request-id">
          Request ID: {{ requestId }}
          <span v-if="reasonCode"> · {{ reasonCode }}</span>
        </p>
      </div>
    </div>
  </article>
</template>
