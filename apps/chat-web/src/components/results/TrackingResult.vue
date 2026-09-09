<script setup lang="ts">
import { computed } from 'vue'
import type { AgentResult, AgentSourceResponse } from '../../agent-api'

import { asRecord, dateTime, text } from './result-data'


const props = defineProps<{
  data: Record<string, unknown>
  status: AgentResult['status']
  provenance: AgentSourceResponse[]
}>()
const sourceLabels = {
  fake_gateway: '合成测试数据',
  external_api: '外部接口（配置声明）',
  unknown: '历史来源未确认',
}
const completenessLabels = {
  complete: '来源声明完整',
  partial: '部分历史',
  unknown: '未确认',
}
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
      <strong>{{ status === 'no_match' ? '本次未返回记录' : text(data.current_status) }}</strong>
    </div>
    <dl class="agent-result-facts">
      <div v-if="data.mail_no"><dt>邮件号</dt><dd>{{ text(data.mail_no) }}</dd></div>
      <div v-if="data.queried_at"><dt>查询时间</dt><dd>{{ dateTime(data.queried_at) }}</dd></div>
    </dl>
    <div v-for="(source, index) in provenance" :key="index" class="agent-tracking-source">
      <dl class="agent-result-facts">
        <div><dt>数据来源</dt><dd>{{ sourceLabels[source.source_type] }} · {{ source.source_name }}</dd></div>
        <div><dt>协议版本</dt><dd>{{ source.source_profile || '未记录' }}</dd></div>
        <div><dt>观察时间</dt><dd>{{ source.queried_at ? dateTime(source.queried_at) : '未记录' }}</dd></div>
        <div><dt>历史完整性</dt><dd>{{ completenessLabels[source.history_completeness ?? 'unknown'] }}</dd></div>
      </dl>
    </div>
    <p v-if="!provenance.length" class="agent-result-empty">该历史结果未记录来源信息，不能据此判断实时性。</p>
    <p v-if="status === 'no_match'" class="agent-result-empty">未返回记录不代表邮件不存在，可稍后发起新查询。</p>
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
    <p class="agent-result-empty">节点反映本次查询返回的记录；重放保留原观察时间，重新查询才会再次取数。</p>
  </section>
</template>
