<script setup lang="ts">
import { computed } from 'vue'

import { dateTime, recordList, safeHttpUrl, text } from './result-data'

const props = defineProps<{
  data: Record<string, unknown>
  type: 'policy' | 'device_price'
}>()
const evidence = computed(() =>
  recordList(props.data.evidence).filter((item) => item.type === props.type),
)
const evidenceIds = computed(() =>
  Array.isArray(props.data.evidence_ids)
    ? props.data.evidence_ids.map((item) => String(item))
    : [],
)

function itemKey(item: Record<string, unknown>, index: number): string {
  return text(item.evidence_id, `${props.type}-${index}`)
}
</script>

<template>
  <section class="agent-result-card" :aria-label="type === 'policy' ? '政策依据' : '设备价格依据'">
    <div class="agent-result-card__heading">
      <span>{{ type === 'policy' ? '政策依据' : '设备价格候选' }}</span>
      <strong>{{ evidence.length || evidenceIds.length }} 条</strong>
    </div>
    <div v-if="type === 'policy' && evidence.length" class="agent-evidence-list">
      <article
        v-for="(item, index) in evidence"
        :key="itemKey(item, index)"
        class="agent-evidence-item"
      >
        <div class="agent-evidence-item__title">
          <strong>{{ text(item.title, '未命名政策资料') }}</strong>
          <code>{{ text(item.evidence_id) }}</code>
        </div>
        <p>{{ text(item.excerpt, '未提供引用摘要') }}</p>
        <dl class="agent-result-facts">
          <div>
            <dt>发布机构</dt>
            <dd>{{ text(item.source_org) }}</dd>
          </div>
          <div>
            <dt>文号</dt>
            <dd>{{ text(item.document_no) }}</dd>
          </div>
          <div>
            <dt>章节</dt>
            <dd>{{ text(item.section_path) }}</dd>
          </div>
          <div>
            <dt>发布日期</dt>
            <dd>{{ text(item.published_at) }}</dd>
          </div>
        </dl>
        <a
          v-if="safeHttpUrl(item.source_url)"
          :href="safeHttpUrl(item.source_url) ?? undefined"
          target="_blank"
          rel="noopener noreferrer"
        >查看公开原文</a>
      </article>
    </div>
    <div v-else-if="type === 'device_price' && evidence.length" class="agent-evidence-list">
      <article
        v-for="(item, index) in evidence"
        :key="itemKey(item, index)"
        class="agent-evidence-item agent-evidence-item--price"
      >
        <div class="agent-evidence-item__title">
          <strong>{{ text(item.title, '未命名设备') }}</strong>
          <code>{{ text(item.evidence_id) }}</code>
        </div>
        <p class="agent-evidence-price">
          <small>{{ text(item.currency, '') }}</small>
          <strong>{{ text(item.price) }}</strong>
          <del v-if="item.original_price">{{ text(item.original_price) }}</del>
        </p>
        <dl class="agent-result-facts">
          <div>
            <dt>型号</dt>
            <dd>{{ text(item.model) }}</dd>
          </div>
          <div>
            <dt>规格</dt>
            <dd>{{ text(item.specification) }}</dd>
          </div>
          <div>
            <dt>来源 / 状态</dt>
            <dd>{{ text(item.source) }} · {{ text(item.availability) }}</dd>
          </div>
          <div>
            <dt>观察时间</dt>
            <dd>{{ dateTime(item.observed_at) }}</dd>
          </div>
        </dl>
        <a
          v-if="safeHttpUrl(item.source_url)"
          :href="safeHttpUrl(item.source_url) ?? undefined"
          target="_blank"
          rel="noopener noreferrer"
        >查看价格来源</a>
      </article>
    </div>
    <div v-else-if="evidenceIds.length" class="agent-evidence-ids">
      <code v-for="id in evidenceIds" :key="id">{{ id }}</code>
    </div>
    <p v-else class="agent-result-empty">结果未包含可公开展示的依据。</p>
  </section>
</template>
