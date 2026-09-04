<script setup lang="ts">
import { regionName, text, dateTime } from './result-data'


defineProps<{ data: Record<string, unknown> }>()
</script>

<template>
  <section class="agent-result-card" aria-label="寄递时限结果">
    <div class="agent-route">
      <div><small>寄件地</small><strong>{{ regionName(data.origin) }}</strong></div>
      <span aria-hidden="true">→</span>
      <div><small>收件地</small><strong>{{ regionName(data.destination) }}</strong></div>
    </div>
    <p class="agent-result-metric">
      <strong>{{ text(data.estimated_duration) }}</strong>
      <span>{{ text(data.duration_unit, '') }}</span>
    </p>
    <dl class="agent-result-facts">
      <div><dt>服务等级</dt><dd>{{ text(data.service_level) }}</dd></div>
      <div><dt>估算依据</dt><dd>{{ text(data.estimate_basis) }}</dd></div>
      <div><dt>查询时间</dt><dd>{{ dateTime(data.queried_at) }}</dd></div>
    </dl>
  </section>
</template>
