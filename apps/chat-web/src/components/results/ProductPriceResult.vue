<script setup lang="ts">
import { computed } from 'vue'
import { validateProductPrice } from '../../product-price-contract'

const props = defineProps<{ data: Record<string, unknown> }>()
const payload = computed(() => {
  try { return validateProductPrice(props.data) } catch { return null }
})
const units: Record<string, string> = { CNY_PER_PIECE: '元 / 当前配置', CNY_PER_500G: '元 / 500克', CNY_PER_KG: '元 / 公斤' }
const natures: Record<string, string> = { RETAIL_OFFER: '零售报价', RETAIL_AVERAGE: '零售均价', WHOLESALE_AVERAGE: '批发均价' }
const availability: Record<string, string> = { ON_SALE: '在售', OUT_OF_STOCK: '缺货', RESERVATION: '预约', PRE_SALE: '预售', COMING_SOON: '即将开售', OFF_SHELF: '已下架', UNKNOWN: '销售状态未知' }
const originalTypes: Record<string, string> = { CROSSED_OUT: '划线价', MSRP: '建议零售价', EXPLICIT_ORIGINAL: '来源原价' }
const specNames: Record<string, string> = { capacity: '存储', memory: '内存', color: '颜色', connectivity: '连接', screen_size: '尺寸', processor: '处理器' }
const regionNames: Record<string, string> = {
  '110000': '北京', '120000': '天津', '130000': '河北', '140000': '山西', '150000': '内蒙古',
  '210000': '辽宁', '220000': '吉林', '230000': '黑龙江', '310000': '上海', '310100': '上海市',
  '320000': '江苏', '330000': '浙江', '340000': '安徽', '350000': '福建', '360000': '江西', '370000': '山东',
  '410000': '河南', '420000': '湖北', '430000': '湖南', '440000': '广东', '450000': '广西', '460000': '海南',
  '500000': '重庆', '510000': '四川', '520000': '贵州', '530000': '云南', '540000': '西藏',
  '610000': '陕西', '620000': '甘肃', '630000': '青海', '640000': '宁夏', '650000': '新疆', XJ_CORPS: '新疆生产建设兵团',
}
function timestamp(value: string, precision = 'INSTANT'): string {
  const date = new Date(value)
  if (precision === 'DAY') return new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit' }).format(date) + '（按日）'
  return date.toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false }) + '（北京时间）'
}
</script>

<template>
  <section v-if="payload" aria-label="商品价格结果" class="product-price-results">
    <article v-for="(item, index) in payload.items" :key="index" class="agent-result-card">
      <h3>{{ item.identity.kind === 'device' ? `${item.identity.brand} ${item.identity.product_name}` : item.identity.commodity_name }}</h3>
      <p v-if="item.identity.kind === 'device'" class="product-price-spec">
        <span v-for="(value, key) in item.identity.specification" :key="key">{{ specNames[key] ?? key }}：{{ value }} </span>
      </p>
      <p v-else>{{ item.identity.source_specification }}<span v-if="item.identity.market_name"> · {{ item.identity.market_name }}</span></p>
      <p class="agent-result-metric" v-if="item.quote.kind === 'priced'">
        <strong>{{ item.quote.current_price }}</strong><span>{{ units[item.quote.quoted_unit] }}</span>
      </p>
      <p v-else class="agent-result-metric"><strong>{{ availability[item.quote.availability] }}</strong><span>暂无当前价格</span></p>
      <p v-if="item.quote.original_price">{{ originalTypes[item.quote.original_price_type] }}：{{ item.quote.original_price }} {{ units[item.quote.quoted_unit] }}</p>
      <p v-if="item.identity.kind === 'fresh' && item.quote.unit_price">统一计价：{{ item.quote.unit_price.amount }} {{ units[item.quote.unit_price.unit] }}；不同来源独立列示，不合并均价。</p>
      <dl class="agent-result-facts">
        <div><dt>价格口径</dt><dd>{{ natures[item.quote.price_nature] }}</dd></div>
        <div><dt>销售状态</dt><dd>{{ availability[item.quote.availability] }}</dd></div>
        <div><dt>报价地区</dt><dd>{{ item.region.scope === 'NATIONAL' ? '全国' : regionNames[item.region.code] ?? `地区代码 ${item.region.code}` }}</dd></div>
        <div><dt>数据日期</dt><dd>{{ timestamp(item.quote.observed_at, item.quote.time_precision) }}</dd></div>
        <div><dt>查询时间</dt><dd>{{ timestamp(item.queried_at) }}</dd></div>
        <div><dt>时效</dt><dd>{{ item.freshness === 'fresh' ? '在配置时效内' : item.freshness === 'stale' ? '数据已超过配置时效' : '时效未知，请核对数据日期' }}</dd></div>
        <div><dt>来源</dt><dd><a :href="item.source.url" target="_blank" rel="noopener noreferrer">{{ item.source.name }}</a> · {{ item.source.profile }}</dd></div>
      </dl>
      <p v-if="item.last_known_price">历史补充（非当前售价）：{{ item.last_known_price.current_price }} {{ units[item.last_known_price.quoted_unit] }}，{{ timestamp(item.last_known_price.observed_at, item.last_known_price.time_precision) }}。</p>
    </article>
    <p v-if="payload.truncated">结果有截断，请补充型号、规格或市场缩小范围。</p>
  </section>
</template>

<style scoped>
.product-price-results { display: grid; gap: 12px; }
.product-price-spec { display: flex; flex-wrap: wrap; gap: 8px; }
h3 { margin: 0 0 8px; }
a { color: inherit; text-decoration: underline; overflow-wrap: anywhere; }
</style>
