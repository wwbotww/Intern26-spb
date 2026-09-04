<script setup lang="ts">
import { dateTime, regionName, text, weightLabel } from './result-data'


defineProps<{ data: Record<string, unknown> }>()
</script>

<template>
  <section class="agent-result-card" aria-label="邮费试算结果">
    <div class="agent-route">
      <div><small>寄件地</small><strong>{{ regionName(data.origin) }}</strong></div>
      <span aria-hidden="true">→</span>
      <div><small>收件地</small><strong>{{ regionName(data.destination) }}</strong></div>
    </div>
    <p class="agent-result-metric agent-result-metric--money">
      <small>{{ text(data.currency, 'CNY') }}</small>
      <strong>{{ text(data.amount) }}</strong>
    </p>
    <dl class="agent-result-facts">
      <div><dt>输入重量</dt><dd>{{ weightLabel(data.input_weight) }}</dd></div>
      <div><dt>计费重量</dt><dd>{{ weightLabel(data.billable_weight) }}</dd></div>
      <div><dt>产品代码</dt><dd>{{ text(data.product_code) }}</dd></div>
      <div><dt>查询时间</dt><dd>{{ dateTime(data.queried_at) }}</dd></div>
    </dl>
  </section>
</template>
