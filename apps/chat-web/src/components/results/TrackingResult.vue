<script setup lang="ts">
import { computed } from 'vue'

import { asRecord, dateTime, text } from './result-data'


const props = defineProps<{ data: Record<string, unknown> }>()
const events = computed(() =>
  Array.isArray(props.data.events)
    ? props.data.events.map((item) => asRecord(item))
    : [],
)
</script>

<template>
  <section class="agent-result-card agent-tracking-result" aria-label="邮件轨迹结果">
    <div class="agent-result-card__heading">
      <span>邮件轨迹</span>
      <strong>{{ text(data.current_status) }}</strong>
    </div>
    <dl class="agent-result-facts">
      <div><dt>邮件号</dt><dd>{{ text(data.mail_no) }}</dd></div>
      <div><dt>查询时间</dt><dd>{{ dateTime(data.queried_at) }}</dd></div>
    </dl>
    <ol v-if="events.length" class="agent-timeline">
      <li v-for="(event, index) in events" :key="`${text(event.occurred_at)}-${index}`">
        <i aria-hidden="true" />
        <div>
          <strong>{{ text(event.description) }}</strong>
          <p>{{ text(event.location, '') }} · {{ dateTime(event.occurred_at) }}</p>
        </div>
      </li>
    </ol>
    <p v-else class="agent-result-empty">当前接口未返回可展示的历史节点。</p>
  </section>
</template>
