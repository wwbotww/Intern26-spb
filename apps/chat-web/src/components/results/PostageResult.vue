<script setup lang="ts">
import type { AgentResult } from '../../agent-api'
import type { PostageQuoteBasisResponse } from '../../generated/agent-api'
import { dateTime, regionName, text, weightLabel } from './result-data'


defineProps<{
  data: Record<string, unknown>
  status: AgentResult['status']
  basis: PostageQuoteBasisResponse | null
}>()
const kinds = { total: '总报价', standard: '标准报价', customer: '客户报价' }
const included = { yes: '已包含', no: '未包含', unknown: '是否包含尚未确认' }
const fees = {
  registration: '挂号费', insurance: '保险费', declared_value: '保价费', inspection: '验关费',
  customs: '报关费', fuel: '燃油附加费', return_receipt: '回执费', password_delivery: '密码投递费',
  printing: '打印费', handling: '处理费',
}
</script>

<template>
  <section class="agent-result-card" aria-label="邮费试算结果">
    <p v-if="status !== 'success'" class="agent-result-empty">本次未取得可展示的完整报价，请根据提示重新查询。</p>
    <template v-else>
      <div class="agent-route">
        <div><small>寄件地</small><strong>{{ regionName(data.origin) }}</strong></div>
        <span aria-hidden="true">→</span>
        <div><small>收件地</small><strong>{{ regionName(data.destination) }}</strong></div>
      </div>
      <p class="agent-result-metric agent-result-metric--money">
        <small>{{ text(data.currency, '币种未记录') }}</small>
        <strong>{{ text(data.amount) }}</strong>
      </p>
      <p class="agent-result-empty">{{ basis ? kinds[basis.amount_kind] : '历史报价口径未记录' }} · 仅供估算，不是最终收费。</p>
      <dl class="agent-result-facts">
        <div><dt>输入重量</dt><dd>{{ weightLabel(data.input_weight) }}</dd></div>
        <div><dt>计费重量</dt><dd>{{ weightLabel(data.billable_weight) }}</dd></div>
        <div><dt>产品代码</dt><dd>{{ text(data.product_code) }}</dd></div>
        <div><dt>查询时间</dt><dd>{{ dateTime(data.queried_at) }}</dd></div>
      </dl>
      <template v-if="basis">
        <p class="agent-result-empty">询价范围：国内按实重、无增值服务。</p>
        <dl class="agent-result-facts">
          <div><dt>数据来源</dt><dd>{{ basis.source.source_type === 'fake_gateway' ? '合成测试数据' : '外部接口（配置声明）' }} · {{ basis.source.source_name }}</dd></div>
          <div><dt>协议版本</dt><dd>{{ basis.source.source_profile }}</dd></div>
          <div><dt>观察时间</dt><dd>{{ dateTime(basis.source.queried_at) }}</dd></div>
          <div v-for="fee in basis.fees" :key="fee.kind">
            <dt>{{ fees[fee.kind] }}</dt><dd>{{ basis.currency }} {{ fee.amount }} · {{ included[fee.included_in_amount] }}</dd>
          </div>
        </dl>
        <p v-if="basis.source.source_type === 'fake_gateway'" class="agent-result-empty">合成报价仅用于验证流程，不可用于寄件决策或收费。</p>
        <p class="agent-result-empty">费用项不自动相加；未返回的费用不代表为零。重放保留原观察时间，重新查询才会再次取数。</p>
      </template>
      <p v-else class="agent-result-empty">该历史结果未记录报价依据及来源，不能判断适用范围、费用构成或实时性。</p>
    </template>
  </section>
</template>
